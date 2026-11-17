// Recording page: record audio or video (as many as you like) -> transcribe -> fill fields -> save.
(function () {
  "use strict";

  var form = document.getElementById("logForm");
  if (!form) { return; }

  var csrf = document.querySelector('meta[name="csrf-token"]').content;
  var I18N = {};
  try { I18N = JSON.parse(form.dataset.i18n || "{}"); } catch (e) { I18N = {}; }
  function tr(text) { return I18N[text] || text; }
  var aiConfigured = form.dataset.aiConfigured === "true";
  var today = form.dataset.today || "";

  var recBtn = document.getElementById("recBtn");
  var recTimer = document.getElementById("recTimer");
  var recState = document.getElementById("recState");
  var preview = document.getElementById("preview");
  var modeButtons = document.querySelectorAll("[data-mode-btn]");
  var mode = "audio";        // "audio" or "video"
  var pickAudio = document.getElementById("pickAudio");
  var pickVideo = document.getElementById("pickVideo");
  var audioFile = document.getElementById("audioFile");
  var videoFile = document.getElementById("videoFile");
  var transcriptEl = document.getElementById("transcript");
  var extractBtn = document.getElementById("extractBtn");
  var extractNote = document.getElementById("extractNote");
  var saveBtn = document.getElementById("saveBtn");
  var formError = document.getElementById("formError");

  var FIELD_IDS = ["customer_name", "location", "contact_person", "visit_date",
                   "observations", "issues", "follow_up", "follow_up_date", "status"];

  var recorder = null;
  var stream = null;
  var chunks = [];
  var mimeType = "";
  var timerId = null;
  var startedAt = 0;
  var recordings = [];        // every recording uploaded on this page: {filename, kind, url}
  var recList = document.getElementById("recList");
  var recListTitle = document.getElementById("recListTitle");
  var busy = false;

  // Browser dictation (Chrome / Edge). Only used when server transcription is off.
  var SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
  var dictation = null;
  var dictationText = "";

  // ---------- helpers ----------
  function setState(text, kind) {
    recState.textContent = text;
    recState.className = "rec-state" + (kind ? " " + kind : "");
  }

  function pad(n) { return n < 10 ? "0" + n : String(n); }

  function tick() {
    var secs = Math.floor((Date.now() - startedAt) / 1000);
    recTimer.textContent = pad(Math.floor(secs / 60)) + ":" + pad(secs % 60);
  }

  function setBusy(value) {
    busy = value;
    recBtn.disabled = value;
    pickAudio.disabled = value;
    pickVideo.disabled = value;
    modeButtons.forEach(function (btn) { btn.disabled = value; });
    extractBtn.disabled = value;
    saveBtn.disabled = value;
  }

  function pickMimeType() {
    if (!window.MediaRecorder || !MediaRecorder.isTypeSupported) { return ""; }
    var options = mode === "video"
      ? ["video/webm;codecs=vp8,opus", "video/webm", "video/mp4"]
      : ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"];
    for (var i = 0; i < options.length; i++) {
      if (MediaRecorder.isTypeSupported(options[i])) { return options[i]; }
    }
    return "";
  }

  function extensionFor(type) {
    if (type.indexOf("mp4") !== -1) { return "mp4"; }
    if (type.indexOf("ogg") !== -1) { return "ogg"; }
    return "webm";
  }

  function setMode(next) {
    if (recBtn.getAttribute("aria-pressed") === "true" || busy) { return; }
    mode = next;
    recBtn.dataset.mode = next;
    modeButtons.forEach(function (btn) {
      btn.setAttribute("aria-pressed", btn.dataset.modeBtn === next ? "true" : "false");
    });
    preview.hidden = true;
  }

  // ---------- list of recordings ----------
  function addRecording(filename, kind, url) {
    var item = { filename: filename, kind: kind, url: url };
    recordings.push(item);

    var li = document.createElement("li");
    var head = document.createElement("div");
    head.className = "media-head";
    var title = document.createElement("strong");
    var count = recordings.filter(function (r) { return r.kind === kind; }).length;
    title.textContent = (kind === "video" ? recList.dataset.labelVideo : recList.dataset.labelAudio) + " " + count;
    var remove = document.createElement("button");
    remove.type = "button";
    remove.className = "btn btn-sm btn-danger";
    remove.style.marginLeft = "auto";
    remove.textContent = recList.dataset.labelRemove;
    head.appendChild(title);
    head.appendChild(remove);

    var player;
    if (kind === "video") {
      player = document.createElement("video");
      player.className = "media-video";
      player.setAttribute("playsinline", "");
      player.preload = "metadata";
    } else {
      player = document.createElement("audio");
      player.preload = "none";
    }
    player.controls = true;
    player.src = url;
    li.appendChild(head);
    li.appendChild(player);
    recList.appendChild(li);
    refreshListVisibility();

    remove.addEventListener("click", function () {
      recordings = recordings.filter(function (r) { return r !== item; });
      li.remove();
      refreshListVisibility();
      // Delete the unsaved file on the server too (best effort).
      postJSON("/api/media/discard", { filename: filename }).catch(function () { /* cleaned up later */ });
    });
  }

  function refreshListVisibility() {
    var any = recordings.length > 0;
    recList.hidden = !any;
    recListTitle.hidden = !any;
  }

  function appendTranscript(text) {
    var current = transcriptEl.value.trim();
    transcriptEl.value = current ? current + "\n\n" + text : text;
  }

  function postJSON(url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
      body: JSON.stringify(body)
    }).then(function (res) {
      return res.json().catch(function () { return { success: false, message: tr("Could not reach the server. Check your connection and try again.") }; })
        .then(function (data) { data._status = res.status; return data; });
    });
  }

  // ---------- recording ----------
  function startRecording() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder) {
      setState(tr("This browser cannot record. Use Chrome, Edge, Firefox or Safari, on https or localhost. You can still upload a file or type notes."), "error");
      return;
    }
    var constraints = mode === "video"
      ? { audio: true, video: { facingMode: { ideal: "environment" } } }
      : { audio: true };
    navigator.mediaDevices.getUserMedia(constraints).then(function (s) {
      stream = s;
      mimeType = pickMimeType();
      var options = {};
      if (mimeType) { options.mimeType = mimeType; }
      if (mode === "video") { options.videoBitsPerSecond = 800000; }   // keeps files under the 25 MB limit longer
      recorder = new MediaRecorder(stream, options);
      mimeType = recorder.mimeType || mimeType || (mode === "video" ? "video/webm" : "audio/webm");
      chunks = [];
      recorder.ondataavailable = function (e) { if (e.data && e.data.size > 0) { chunks.push(e.data); } };
      recorder.onstop = onRecordingStopped;
      recorder.start();

      dictationText = "";
      if (!aiConfigured && SpeechRec) { startDictation(); }

      startedAt = Date.now();
      tick();
      timerId = setInterval(tick, 250);
      recBtn.setAttribute("aria-pressed", "true");
      recBtn.setAttribute("aria-label", recBtn.dataset.labelStop);
      modeButtons.forEach(function (btn) { btn.disabled = true; });
      if (mode === "video") {
        preview.srcObject = stream;      // live camera view while recording
        preview.muted = true;
        preview.controls = false;
        preview.hidden = false;
        var playing = preview.play();
        if (playing && playing.catch) { playing.catch(function () { /* autoplay may be blocked; recording still works */ }); }
      } else {
        preview.hidden = true;
      }
      setState(tr("Recording. Speak now, then tap the button to stop."));
    }).catch(function (err) {
      var denied = err && (err.name === "NotAllowedError" || err.name === "SecurityError");
      setState(denied
        ? tr("Camera or microphone access is blocked. Allow it in your browser's site settings, then try again.")
        : tr("Could not start the recording:") + " " + (err && err.message ? err.message : ""), "error");
    });
  }

  function stopRecording() {
    if (recorder && recorder.state !== "inactive") { recorder.stop(); }
    if (stream) { stream.getTracks().forEach(function (t) { t.stop(); }); }
    if (dictation) { try { dictation.stop(); } catch (e) { /* already stopped */ } }
    clearInterval(timerId);
    modeButtons.forEach(function (btn) { btn.disabled = false; });
    recBtn.setAttribute("aria-pressed", "false");
    recBtn.setAttribute("aria-label", recBtn.dataset.labelStart);
  }

  function onRecordingStopped() {
    var blob = new Blob(chunks, { type: mimeType });
    var isVideo = mode === "video";
    preview.srcObject = null;       // live camera view is done; the recording is listed below
    preview.hidden = true;
    uploadAudio(blob, "recording." + extensionFor(mimeType), isVideo);
  }

  function startDictation() {
    try {
      dictation = new SpeechRec();
      dictation.continuous = true;
      dictation.interimResults = false;
      dictation.lang = navigator.language || "en-IN";
      dictation.onresult = function (e) {
        for (var i = e.resultIndex; i < e.results.length; i++) {
          if (e.results[i].isFinal) { dictationText += e.results[i][0].transcript + " "; }
        }
      };
      dictation.onerror = function () { /* dictation is a best-effort extra */ };
      dictation.start();
    } catch (e) {
      dictation = null;
    }
  }

  // ---------- upload + transcribe ----------
  function uploadAudio(blob, filename, isVideo) {
    setBusy(true);
    setState(aiConfigured ? tr("Transcribing your recording…") : tr("Saving your recording…"));
    var data = new FormData();
    data.append(isVideo ? "video" : "audio", blob, filename);

    return fetch("/api/transcribe", { method: "POST", headers: { "X-CSRFToken": csrf }, body: data })
      .then(function (res) {
        return res.json().catch(function () { return { success: false, message: tr("Could not reach the server. Check your connection and try again.") }; });
      })
      .then(function (result) {
        if (result.filename) {
          addRecording(result.filename, isVideo ? "video" : "audio", URL.createObjectURL(blob));
        }
        if (!result.success) {
          setState(result.message || tr("Transcription failed."), "error");
          return null;
        }
        var text = result.transcript || (!aiConfigured ? dictationText.trim() : "");
        if (text) {
          appendTranscript(text);
          setState(tr("Transcript ready. Filling the fields…"), "ok");
          return extractFields();
        }
        setState(result.message || tr("No speech was detected. Try again, or type your notes."), "error");
        return null;
      })
      .catch(function () {
        setState(tr("Could not reach the server. Check your connection and try again."), "error");
      })
      .then(function () { setBusy(false); });
  }

  // ---------- extract ----------
  function clearAiMarks() {
    FIELD_IDS.forEach(function (id) {
      var wrap = document.querySelector('[data-field="' + id + '"]');
      if (wrap) { wrap.classList.remove("ai-filled"); }
    });
  }

  function extractFields() {
    var text = transcriptEl.value.trim();
    if (!text) {
      extractNote.textContent = tr("Add a transcript first: record, upload audio, or type your notes.");
      return Promise.resolve();
    }
    extractNote.textContent = tr("Reading your notes…");
    return postJSON("/api/extract", { transcript: text }).then(function (result) {
      if (!result.success) {
        extractNote.textContent = result.message || tr("Could not read the notes.");
        setState(result.message || tr("Could not read the notes."), "error");
        return;
      }
      clearAiMarks();
      var filled = 0;
      FIELD_IDS.forEach(function (id) {
        var value = result.fields[id];
        var el = document.getElementById(id);
        if (!el || !value) { return; }
        el.value = value;
        filled += 1;
        var wrap = document.querySelector('[data-field="' + id + '"]');
        if (wrap) { wrap.classList.add("ai-filled"); }
      });
      var how = result.method === "ai" ? tr("AI") : tr("basic matching");
      extractNote.textContent = tr("Filled {n} field(s) using {how}. Fields marked with a green edge came from your notes; please check them.")
        .replace("{n}", filled).replace("{how}", how) + (result.warning ? " " + result.warning : "");
      setState(tr("Check the visit details, then save."), "ok");
    }).catch(function () {
      extractNote.textContent = tr("Could not reach the server. Check your connection and try again.");
    });
  }

  // ---------- save ----------
  function showErrors(errors) {
    document.querySelectorAll("[data-error-for]").forEach(function (el) { el.textContent = ""; });
    document.querySelectorAll(".has-error").forEach(function (el) { el.classList.remove("has-error"); });
    var first = null;
    Object.keys(errors || {}).forEach(function (name) {
      var msg = document.querySelector('[data-error-for="' + name + '"]');
      var wrap = document.querySelector('[data-field="' + name + '"]');
      if (msg) { msg.textContent = errors[name]; }
      if (wrap) { wrap.classList.add("has-error"); if (!first) { first = wrap; } }
    });
    if (first) {
      var input = first.querySelector("input, select, textarea");
      if (input) { input.focus(); }
    }
  }

  function saveLog(event) {
    event.preventDefault();
    formError.hidden = true;
    var payload = {
      transcript: transcriptEl.value,
      media: recordings.map(function (r) { return r.filename; })
    };
    FIELD_IDS.forEach(function (id) { payload[id] = document.getElementById(id).value; });
    if (!payload.visit_date) { payload.visit_date = today; }

    setBusy(true);
    saveBtn.textContent = tr("Saving…");
    postJSON("/api/logs", payload).then(function (result) {
      if (result.success) {
        window.location.href = result.redirect;
        return;
      }
      showErrors(result.errors);
      formError.textContent = result.message || tr("Could not save the visit log.");
      formError.hidden = false;
      setBusy(false);
      saveBtn.textContent = tr("Save visit log");
    }).catch(function () {
      formError.textContent = tr("Could not reach the server. Your entries are still here; try again.");
      formError.hidden = false;
      setBusy(false);
      saveBtn.textContent = tr("Save visit log");
    });
  }

  // ---------- wiring ----------
  recBtn.addEventListener("click", function () {
    if (busy) { return; }
    if (recBtn.getAttribute("aria-pressed") === "true") { stopRecording(); } else { startRecording(); }
  });
  modeButtons.forEach(function (btn) {
    btn.addEventListener("click", function () { setMode(btn.dataset.modeBtn); });
  });
  function wireFilePicker(button, input, isVideo) {
    button.addEventListener("click", function () { input.click(); });
    input.addEventListener("change", function () {
      var files = Array.prototype.slice.call(input.files || []);
      if (!files.length) { return; }
      // Upload the chosen files one after another so each gets transcribed.
      files.reduce(function (chain, file) {
        return chain.then(function () { return uploadAudio(file, file.name, isVideo); });
      }, Promise.resolve()).then(function () { input.value = ""; });
    });
  }
  wireFilePicker(pickAudio, audioFile, false);
  wireFilePicker(pickVideo, videoFile, true);
  extractBtn.addEventListener("click", function () {
    setBusy(true);
    extractFields().then(function () { setBusy(false); });
  });
  form.addEventListener("submit", saveLog);
})();
