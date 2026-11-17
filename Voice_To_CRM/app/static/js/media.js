// Visit page: record more audio or video and add it to this visit (as many as you like).
(function () {
  "use strict";

  var box = document.getElementById("liveRec");
  if (!box) { return; }

  var csrf = document.querySelector('meta[name="csrf-token"]').content;
  var btn = document.getElementById("liveBtn");
  var timerEl = document.getElementById("liveTimer");
  var stateEl = document.getElementById("liveState");
  var previewBox = document.getElementById("livePreview");
  var modeButtons = box.querySelectorAll("[data-live-mode]");
  var mode = "audio";
  var recorder = null, stream = null, chunks = [], mimeType = "", timerId = null, startedAt = 0;
  var preview = null;

  function say(text, kind) {
    stateEl.textContent = text;
    stateEl.className = "small " + (kind === "error" ? "text-danger" : "muted");
  }
  function pad(n) { return n < 10 ? "0" + n : String(n); }
  function tick() {
    var secs = Math.floor((Date.now() - startedAt) / 1000);
    timerEl.textContent = pad(Math.floor(secs / 60)) + ":" + pad(secs % 60);
  }
  function setModeButtons(disabled) {
    modeButtons.forEach(function (b) { b.disabled = disabled; });
  }
  function pickMime() {
    if (!window.MediaRecorder || !MediaRecorder.isTypeSupported) { return ""; }
    var options = mode === "video"
      ? ["video/webm;codecs=vp8,opus", "video/webm", "video/mp4"]
      : ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"];
    for (var i = 0; i < options.length; i++) {
      if (MediaRecorder.isTypeSupported(options[i])) { return options[i]; }
    }
    return "";
  }
  function extFor(type) {
    if (type.indexOf("mp4") !== -1) { return "mp4"; }
    if (type.indexOf("ogg") !== -1) { return "ogg"; }
    return "webm";
  }
  function removePreview() {
    if (preview) { preview.srcObject = null; preview.remove(); preview = null; }
  }

  modeButtons.forEach(function (b) {
    b.addEventListener("click", function () {
      if (recorder && recorder.state !== "inactive") { return; }
      mode = b.dataset.liveMode;
      modeButtons.forEach(function (x) {
        x.setAttribute("aria-pressed", x === b ? "true" : "false");
      });
    });
  });

  function start() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder) {
      say(box.dataset.msgUnsupported, "error");
      return;
    }
    var constraints = mode === "video"
      ? { audio: true, video: { facingMode: { ideal: "environment" } } }
      : { audio: true };
    navigator.mediaDevices.getUserMedia(constraints).then(function (s) {
      stream = s;
      mimeType = pickMime();
      var options = {};
      if (mimeType) { options.mimeType = mimeType; }
      if (mode === "video") { options.videoBitsPerSecond = 800000; }
      recorder = new MediaRecorder(stream, options);
      mimeType = recorder.mimeType || mimeType || (mode === "video" ? "video/webm" : "audio/webm");
      chunks = [];
      recorder.ondataavailable = function (e) { if (e.data && e.data.size > 0) { chunks.push(e.data); } };
      recorder.onstop = upload;
      recorder.start();
      startedAt = Date.now();
      tick();
      timerId = setInterval(tick, 250);
      btn.textContent = box.dataset.labelStop;
      setModeButtons(true);
      if (mode === "video") {
        preview = document.createElement("video");
        preview.className = "preview";
        preview.muted = true;
        preview.setAttribute("playsinline", "");
        preview.srcObject = stream;
        previewBox.appendChild(preview);
        var p = preview.play();
        if (p && p.catch) { p.catch(function () { /* recording still works */ }); }
      }
      say(box.dataset.msgRecording);
    }).catch(function (err) {
      var denied = err && (err.name === "NotAllowedError" || err.name === "SecurityError");
      say(denied ? box.dataset.msgBlocked : (err && err.message) || "", "error");
    });
  }

  function stop() {
    if (recorder && recorder.state !== "inactive") { recorder.stop(); }
    if (stream) { stream.getTracks().forEach(function (t) { t.stop(); }); }
    clearInterval(timerId);
  }

  function upload() {
    removePreview();
    btn.disabled = true;
    say(box.dataset.msgSaving);
    var blob = new Blob(chunks, { type: mimeType });
    var data = new FormData();
    data.append("media", blob, "recording." + extFor(mimeType));
    fetch(box.dataset.url, {
      method: "POST",
      headers: { "X-CSRFToken": csrf, "X-Requested-With": "fetch" },
      body: data
    }).then(function (res) {
      return res.json().catch(function () { return { success: false, message: box.dataset.msgFailed }; });
    }).then(function (result) {
      if (result.success) { window.location.reload(); return; }
      say(result.message || box.dataset.msgFailed, "error");
      btn.disabled = false;
      btn.textContent = box.dataset.labelStart;
      setModeButtons(false);
    }).catch(function () {
      say(box.dataset.msgFailed, "error");
      btn.disabled = false;
      btn.textContent = box.dataset.labelStart;
      setModeButtons(false);
    });
  }

  btn.addEventListener("click", function () {
    if (recorder && recorder.state === "recording") { stop(); } else { start(); }
  });
})();
