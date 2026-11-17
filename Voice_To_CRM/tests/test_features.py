import glob
import html as htmllib
import json
import re
import smtplib
import unittest
from datetime import date, timedelta
from unittest import mock

from tests.test_app import Base

HINDI = dict(customer_name="शर्मा ट्रेडर्स", location="नोएडा", observations="स्टॉक कम है",
             transcript="आज शर्मा जी से मिले")


class ReportTests(Base):
    def make(self, tok, **over):
        return self.api("post", "/api/logs", tok, json=dict(self.LOG, **over)).get_json()["id"]

    def test_single_report(self):
        tok = self.signed_in()
        lid = self.make(tok, **HINDI)
        r = self.c.get(f"/logs/{lid}/report")
        page = r.get_data(as_text=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn("शर्मा ट्रेडर्स", page)             # Hindi text survives in the report
        self.assertIn("आज शर्मा जी से मिले", page)         # transcript included for a single report
        self.assertIn("data-print", page)
        self.assertNotIn("<script>", page)                 # CSP-safe
        self.assertEqual(self.c.get("/logs/999/report").status_code, 404)

    def test_list_report_respects_filters_and_scope(self):
        tok = self.signed_in()
        self.make(tok, customer_name="Alpha Co", status="Completed")
        self.make(tok, customer_name="Beta Co", status="Pending", transcript="SECRET-TRANSCRIPT")
        page = self.c.get("/logs/report?status=Completed").get_data(as_text=True)
        self.assertIn("Alpha Co", page)
        self.assertNotIn("Beta Co", page)
        self.assertNotIn("SECRET-TRANSCRIPT", self.c.get("/logs/report").get_data(as_text=True))
        other = self.app.test_client()
        self.register(other, email="b@x.com", name="Bob")
        self.login(other, email="b@x.com")
        self.assertNotIn("Alpha Co", other.get("/logs/report").get_data(as_text=True))
        self.assertEqual(self.app.test_client().get("/logs/report").status_code, 302)


class CustomerTests(Base):
    def test_customer_detail_and_prefill(self):
        tok = self.signed_in()
        past = (date.today() - timedelta(days=3)).isoformat()
        mk = lambda **o: self.api("post", "/api/logs", tok, json=dict(self.LOG, **o))
        mk(visit_date=past, follow_up_date=past, status="Follow-up Required", contact_person="Mr Gupta")
        mk(customer_name="SHARMA TRADERS", location="Delhi", status="Completed", contact_person="Mr Sharma")
        mk(customer_name="Other Co")
        r = self.c.get("/customer?name=sharma traders")
        page = r.get_data(as_text=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn("Mr Gupta", page)
        self.assertIn("Mr Sharma", page)
        self.assertNotIn("Other Co", page)
        from app import db
        from app.repositories import logs
        with self.app.test_request_context():
            user = db.get_db().one("SELECT * FROM users")
            d = logs.customer_detail(user, "Sharma Traders")
        self.assertEqual((d["visits"], d["needs_followup"], d["completed"], d["overdue"]), (2, 1, 1, 1))
        self.assertEqual(self.c.get("/customer?name=Nobody").status_code, 404)
        self.assertEqual(self.c.get("/customer").status_code, 404)
        rec = self.c.get("/record?customer=Acme%20Ltd&location=Pune&contact=Mr%20Rao").get_data(as_text=True)
        for value in ("Acme Ltd", "Pune", "Mr Rao"):
            self.assertIn(f'value="{value}"', rec)

    def test_customer_scope_between_users(self):
        tok = self.signed_in()
        self.api("post", "/api/logs", tok, json=self.LOG)
        other = self.app.test_client()
        self.register(other, email="b@x.com", name="Bob")
        self.login(other, email="b@x.com")
        self.assertEqual(other.get("/customer?name=Sharma Traders").status_code, 404)

    def test_logs_customer_filter(self):
        tok = self.signed_in()
        self.api("post", "/api/logs", tok, json=self.LOG)
        self.api("post", "/api/logs", tok, json=dict(self.LOG, customer_name="Beta"))
        page = self.c.get("/logs?customer=beta").get_data(as_text=True)
        self.assertIn("1 visit log ", page)
        self.assertIn("Beta", page)


class ReminderTests(Base):
    SMTP = dict(SMTP_HOST="smtp.test", SMTP_PORT=587, SMTP_USER="u", SMTP_PASSWORD="p",
                SMTP_FROM="crm@test.com", SMTP_SECURITY="starttls")

    def setUp(self):
        super().setUp()
        self.app.config.update(self.SMTP)

    def seed(self, tok):
        today = date.today()
        mk = lambda **o: self.api("post", "/api/logs", tok, json=dict(self.LOG, **o))
        mk(customer_name="Late Co", follow_up="Call back", follow_up_date=(today - timedelta(days=2)).isoformat())
        mk(customer_name="Tomorrow Co", follow_up_date=(today + timedelta(days=1)).isoformat())
        mk(customer_name="Far Co", follow_up_date=(today + timedelta(days=9)).isoformat())
        mk(customer_name="Done Co", status="Completed", follow_up_date=(today - timedelta(days=1)).isoformat())
        mk(customer_name="No Date Co")

    def run_reminders(self, **kw):
        from app.services import reminders
        with self.app.app_context():
            return reminders.run_all(**kw)

    def test_digest_content_and_dedupe(self):
        tok = self.signed_in()
        self.seed(tok)
        with mock.patch("app.services.mailer.smtplib.SMTP") as smtp:
            first = self.run_reminders()
            again = self.run_reminders()
            forced = self.run_reminders(force=True)
        self.assertEqual((first["sent"], first["skipped"]), (1, 0))
        self.assertEqual((again["sent"], again["skipped"]), (0, 1))    # once per day
        self.assertEqual(forced["sent"], 1)
        server = smtp.return_value
        server.starttls.assert_called()
        server.login.assert_called_with("u", "p")
        msg = server.send_message.call_args_list[0][0][0]
        body = msg.get_body(("plain",)).get_content()
        self.assertEqual(msg["To"], "a@x.com")
        self.assertIn("2 to do", msg["Subject"])
        self.assertIn("1 overdue", msg["Subject"])
        self.assertIn("Late Co", body)
        self.assertIn("OVERDUE by 2 days", body)
        self.assertIn("Tomorrow Co", body)
        for excluded in ("Far Co", "Done Co", "No Date Co"):
            self.assertNotIn(excluded, body)

    def test_only_own_followups_and_nothing_due(self):
        tok = self.signed_in()
        self.seed(tok)
        other = self.app.test_client()
        self.register(other, email="b@x.com", name="Bob")
        self.login(other, email="b@x.com")
        with mock.patch("app.services.mailer.smtplib.SMTP") as smtp:
            res = self.run_reminders()
        self.assertEqual(res["sent"], 1)    # Bob has nothing due, so he gets no email
        self.assertEqual(smtp.return_value.send_message.call_count, 1)

    def test_smtp_errors_are_reported_not_marked_sent(self):
        tok = self.signed_in()
        self.seed(tok)
        with mock.patch("app.services.mailer.smtplib.SMTP", side_effect=smtplib.SMTPConnectError(421, "nope")):
            res = self.run_reminders()
        self.assertEqual((res["sent"], res["failed"]), (0, 1))
        with mock.patch("app.services.mailer.smtplib.SMTP"):
            self.assertEqual(self.run_reminders()["sent"], 1)     # retried later the same day

    def test_dashboard_button(self):
        tok = self.signed_in()
        self.seed(tok)
        self.assertIn("Email my follow-ups", self.c.get("/dashboard").get_data(as_text=True))
        with mock.patch("app.services.mailer.smtplib.SMTP"):
            r = self.c.post("/reminders/send", data=dict(csrf_token=tok), follow_redirects=True)
        self.assertIn("Reminder sent to a@x.com", r.get_data(as_text=True))
        self.assertEqual(self.c.post("/reminders/send").status_code, 400)    # CSRF

    def test_not_configured(self):
        tok = self.signed_in()
        self.app.config.update(SMTP_HOST=None)
        page = self.c.get("/dashboard").get_data(as_text=True)
        self.assertNotIn("Email my follow-ups", page)
        self.assertIn("Email reminders are off", page)       # admin sees the hint
        r = self.c.post("/reminders/send", data=dict(csrf_token=tok), follow_redirects=True)
        self.assertIn("Email is not set up", r.get_data(as_text=True))


class VideoTests(Base):
    def media(self, field, name, size=3000):
        import io
        return {field: (io.BytesIO(b"x" * size), name)}

    def upload(self, tok, field, name, **kw):
        return self.api("post", "/api/transcribe", tok, data=self.media(field, name, **kw),
                        content_type="multipart/form-data")

    def test_video_upload_save_play_delete(self):
        import os
        tok = self.signed_in()
        j = self.upload(tok, "video", "clip.mp4").get_json()
        self.assertTrue(j["success"])
        self.assertEqual(j["kind"], "video")
        self.assertRegex(j["filename"], r"^v_[0-9a-f]{32}\.mp4$")
        lid = self.api("post", "/api/logs", tok, json=dict(self.LOG, audio_file=j["filename"])).get_json()["id"]
        page = self.c.get(f"/logs/{lid}").get_data(as_text=True)
        self.assertIn("<video", page)
        self.assertNotIn("<audio", page)
        r = self.c.get(f"/logs/{lid}/audio")
        self.assertEqual((r.status_code, r.mimetype, len(r.get_data())), (200, "video/mp4", 3000))
        path = os.path.join(self.app.config["UPLOAD_DIR"], "1", j["filename"])
        self.assertTrue(os.path.exists(path))
        self.c.post(f"/logs/{lid}/delete", data=dict(csrf_token=tok))
        self.assertFalse(os.path.exists(path))             # video file removed with the log

    def test_webm_video_and_audio_still_work(self):
        tok = self.signed_in()
        v = self.upload(tok, "video", "recording.webm").get_json()
        a = self.upload(tok, "audio", "recording.webm").get_json()
        self.assertTrue(v["filename"].startswith("v_") and v["kind"] == "video")
        self.assertFalse(a["filename"].startswith("v_")) 
        self.assertEqual(a["kind"], "audio")
        lid = self.api("post", "/api/logs", tok, json=dict(self.LOG, audio_file=a["filename"])).get_json()["id"]
        page = self.c.get(f"/logs/{lid}").get_data(as_text=True)
        self.assertIn("<audio", page)
        self.assertNotIn("<video", page)
        self.assertEqual(self.c.get(f"/logs/{lid}/audio").mimetype, "audio/webm")

    def test_rejects_bad_video_and_traversal(self):
        tok = self.signed_in()
        r = self.upload(tok, "video", "clip.mov")
        self.assertEqual(r.status_code, 400)
        self.assertIn("mp4 or webm", r.get_json()["message"])
        self.assertEqual(self.upload(tok, "video", "clip.mp4", size=10).status_code, 400)   # empty
        lid = self.api("post", "/api/logs", tok, json=dict(self.LOG, audio_file="v_../../etc/passwd")).get_json()["id"]
        self.assertEqual(self.c.get(f"/logs/{lid}/audio").status_code, 404)

    def test_video_is_private_to_owner(self):
        tok = self.signed_in()
        j = self.upload(tok, "video", "clip.mp4").get_json()
        lid = self.api("post", "/api/logs", tok, json=dict(self.LOG, audio_file=j["filename"])).get_json()["id"]
        other = self.app.test_client()
        self.register(other, email="b@x.com", name="Bob")
        self.login(other, email="b@x.com")
        self.assertEqual(other.get(f"/logs/{lid}/audio").status_code, 404)

    def test_record_page_has_video_option_in_both_languages(self):
        self.signed_in()
        en = self.c.get("/record").get_data(as_text=True)
        self.assertIn('data-mode-btn="video"', en)
        self.assertIn("Upload video", en)
        self.assertIn('id="videoFile"', en)
        self.c.get("/lang/hi")
        hi = self.c.get("/record").get_data(as_text=True)
        self.assertIn("वीडियो", hi)
        self.assertIn("वीडियो अपलोड करें", hi)


class AttachMediaTests(Base):
    def post_media(self, client, lid, tok, name="clip.mp4", mimetype="video/mp4", size=3000):
        import io
        return client.post(f"/logs/{lid}/media", data={
            "csrf_token": tok, "media": (io.BytesIO(b"x" * size), name, mimetype)},
            content_type="multipart/form-data", follow_redirects=True)

    def make_log(self, tok):
        return self.api("post", "/api/logs", tok, json=self.LOG).get_json()["id"]

    def media_rows(self, lid):
        from app import db
        with self.app.app_context():
            conn = db.connect(self.app.config)
            try:
                return conn.all("SELECT * FROM log_media WHERE log_id = %s ORDER BY id", (lid,))
            finally:
                conn.close()

    def test_add_many_and_remove_one(self):
        import os
        tok = self.signed_in()
        lid = self.make_log(tok)
        page = self.c.get(f"/logs/{lid}").get_data(as_text=True)
        self.assertIn("No recording attached.", page)
        self.assertIn("Add recording", page)
        self.assertNotIn("Remove recording", page)

        r = self.post_media(self.c, lid, tok)                         # add a video
        self.assertIn("Recording added.", r.get_data(as_text=True))
        self.post_media(self.c, lid, tok, name="note.mp3", mimetype="audio/mpeg")   # add audio too
        self.post_media(self.c, lid, tok, name="clip2.mp4")                          # and another video
        folder = os.path.join(self.app.config["UPLOAD_DIR"], "1")
        self.assertEqual(len(os.listdir(folder)), 3)                  # nothing was replaced
        page = self.c.get(f"/logs/{lid}").get_data(as_text=True)
        self.assertEqual(page.count("<video class=\"media-video\""), 2)
        self.assertEqual(page.count("<audio controls"), 1)
        self.assertIn("3 recordings", page)

        media = self.media_rows(lid)
        self.assertEqual([m["kind"] for m in media], ["video", "audio", "video"])
        r = self.c.get(f"/logs/{lid}/media/{media[1]['id']}")
        self.assertEqual((r.status_code, r.mimetype), (200, "audio/mpeg"))

        r = self.c.post(f"/logs/{lid}/media/{media[0]['id']}/delete", data=dict(csrf_token=tok),
                        follow_redirects=True)
        self.assertIn("Recording removed.", r.get_data(as_text=True))
        self.assertEqual(len(os.listdir(folder)), 2)                  # only that file is gone
        self.assertEqual([m["id"] for m in self.media_rows(lid)], [media[1]["id"], media[2]["id"]])
        self.assertEqual(self.c.get(f"/logs/{lid}/media/{media[0]['id']}").status_code, 404)

    def test_select_several_files_at_once(self):
        import io
        tok = self.signed_in()
        lid = self.make_log(tok)
        r = self.c.post(f"/logs/{lid}/media", data={
            "csrf_token": tok,
            "media": [(io.BytesIO(b"x" * 3000), "a.mp3", "audio/mpeg"),
                      (io.BytesIO(b"x" * 3000), "b.mp4", "video/mp4"),
                      (io.BytesIO(b"x" * 3000), "c.wav", "audio/wav")]},
            content_type="multipart/form-data", follow_redirects=True)
        self.assertIn("3 recordings added.", r.get_data(as_text=True))
        self.assertEqual(len(self.media_rows(lid)), 3)

    def test_browser_recording_upload_returns_json(self):
        import io
        tok = self.signed_in()
        lid = self.make_log(tok)
        r = self.c.post(f"/logs/{lid}/media", data={
            "media": (io.BytesIO(b"x" * 3000), "recording.webm", "video/webm;codecs=vp8,opus")},
            headers={"X-CSRFToken": tok, "X-Requested-With": "fetch"},
            content_type="multipart/form-data")
        self.assertEqual((r.status_code, r.get_json()["added"]), (200, 1))
        self.assertEqual(self.media_rows(lid)[0]["kind"], "video")
        bad = self.c.post(f"/logs/{lid}/media", data={"media": (io.BytesIO(b"x" * 10), "r.webm", "audio/webm")},
                          headers={"X-CSRFToken": tok, "X-Requested-With": "fetch"},
                          content_type="multipart/form-data")
        self.assertEqual(bad.status_code, 400)
        self.assertFalse(bad.get_json()["success"])

    def test_save_new_log_with_many_recordings_and_discard_one(self):
        import io, os
        tok = self.signed_in()
        names = []
        for field, name in (("audio", "a.webm"), ("video", "b.mp4"), ("audio", "c.mp3"), ("video", "d.webm")):
            j = self.api("post", "/api/transcribe", tok, data={field: (io.BytesIO(b"x" * 3000), name)},
                         content_type="multipart/form-data").get_json()
            names.append(j["filename"])
        folder = os.path.join(self.app.config["UPLOAD_DIR"], "1")
        self.assertEqual(self.api("post", "/api/media/discard", tok, json={"filename": names[3]}).status_code, 200)
        self.assertFalse(os.path.exists(os.path.join(folder, names[3])))
        junk = ["../../etc/passwd", names[0], 123, "0" * 32 + ".webm"]   # duplicate, bad type, missing file
        lid = self.api("post", "/api/logs", tok, json=dict(self.LOG, media=names[:3] + junk)).get_json()["id"]
        self.assertEqual([m["filename"] for m in self.media_rows(lid)], names[:3])
        # a saved recording cannot be discarded through the API
        self.api("post", "/api/media/discard", tok, json={"filename": names[0]})
        self.assertTrue(os.path.exists(os.path.join(folder, names[0])))
        self.c.post(f"/logs/{lid}/delete", data=dict(csrf_token=tok))
        self.assertEqual(os.listdir(folder), [])                      # every file removed with the log

    def test_old_single_recording_moves_into_new_table(self):
        import os
        from app import db
        tok = self.signed_in()
        lid = self.make_log(tok)
        with self.app.app_context():
            conn = db.connect(self.app.config)
            conn.execute("UPDATE field_logs SET audio_file = %s WHERE id = %s", ("v_" + "a" * 32 + ".mp4", lid))
            conn.commit()
            db.init_schema(self.app.config)
            db.init_schema(self.app.config)                           # running twice adds nothing
            conn.close()
        media = self.media_rows(lid)
        self.assertEqual([(m["filename"], m["kind"]) for m in media], [("v_" + "a" * 32 + ".mp4", "video")])

    def test_rejects_bad_files_and_missing_csrf_and_foreign_logs(self):
        tok = self.signed_in()
        lid = self.make_log(tok)
        bad = self.post_media(self.c, lid, tok, name="clip.mov", mimetype="video/quicktime")
        self.assertIn("mp4 or webm", bad.get_data(as_text=True))
        small = self.post_media(self.c, lid, tok, size=10)
        self.assertIn("empty", small.get_data(as_text=True))
        import io
        no_csrf = self.c.post(f"/logs/{lid}/media", data={"media": (io.BytesIO(b"x" * 3000), "a.mp4", "video/mp4")},
                              content_type="multipart/form-data")
        self.assertEqual(no_csrf.status_code, 400)
        other = self.app.test_client()
        self.register(other, email="b@x.com", name="Bob")
        self.login(other, email="b@x.com")
        otok = self.token(other, "/dashboard")
        self.assertEqual(self.post_media(other, lid, otok).status_code, 404)
        self.assertEqual(other.post(f"/logs/{lid}/media/1/delete", data=dict(csrf_token=otok)).status_code, 404)

    def test_admin_attach_to_another_users_log_is_playable_by_owner(self):
        self.register()                                   # first account = admin
        bob = self.app.test_client()
        self.register(bob, email="b@x.com", name="Bob")
        self.login(bob, email="b@x.com")
        btok = self.token(bob, "/dashboard")
        lid = bob.post("/api/logs", headers={"X-CSRFToken": btok}, json=self.LOG).get_json()["id"]
        self.login()
        atok = self.token(path="/dashboard")
        self.post_media(self.c, lid, atok)
        r = bob.get(f"/logs/{lid}/audio")                 # stored in Bob's folder, so Bob can play it
        self.assertEqual((r.status_code, r.mimetype), (200, "video/mp4"))

    def test_hindi_labels(self):
        tok = self.signed_in()
        lid = self.make_log(tok)
        self.c.get("/lang/hi")
        page = self.c.get(f"/logs/{lid}").get_data(as_text=True)
        self.assertIn("रिकॉर्डिंग जोड़ें", page)
        self.assertIn("कोई रिकॉर्डिंग जुड़ी नहीं है।", page)


class AccountTypeTests(Base):
    def signup(self, email, role="user", code="", name="Person"):
        client = self.app.test_client()
        tok = self.token(client, "/register")
        r = client.post("/register", data=dict(csrf_token=tok, name=name, email=email, password="password1",
                                               confirm="password1", role=role, admin_code=code))
        return client, r

    def roles(self):
        from app import db
        with self.app.app_context():
            return {r["email"]: r["role"] for r in db.get_db().all("SELECT email, role FROM users")}

    def sign_in(self, client, email):
        tok = self.token(client)
        client.post("/login", data=dict(csrf_token=tok, email=email, password="password1"))
        return self.token(client, "/dashboard")

    def setUp(self):
        super().setUp()
        from app.blueprints import auth
        auth._FAILED.clear()
        self.app.config["ADMIN_SIGNUP_CODE"] = "letmein-123"
        self.signup("first@x.com", role="user")                      # first account: always administrator

    def test_first_account_is_admin_whatever_is_chosen(self):
        self.assertEqual(self.roles()["first@x.com"], "admin")

    def test_signup_page_offers_both_types_in_both_languages(self):
        client = self.app.test_client()
        page = client.get("/register").get_data(as_text=True)
        for text in ("User account", "Administrator account", "Admin code"):
            self.assertIn(text, page)
        client.get("/lang/hi")
        hi = client.get("/register").get_data(as_text=True)
        self.assertIn("एडमिनिस्ट्रेटर खाता", hi)
        self.assertIn("एडमिन कोड", hi)

    def test_user_type_is_default_and_admin_needs_the_code(self):
        self.signup("u@x.com")
        self.signup("bad@x.com", role="admin", code="wrong")
        r = self.signup("nocode@x.com", role="admin")[1]
        self.assertIn("The admin code is wrong.", r.get_data(as_text=True))
        self.signup("good@x.com", role="admin", code="letmein-123")
        self.assertEqual(self.roles(), {"first@x.com": "admin", "u@x.com": "user", "good@x.com": "admin"})

    def test_admin_signup_off_without_a_code_and_code_guessing_is_limited(self):
        self.app.config["ADMIN_SIGNUP_CODE"] = None
        r = self.signup("a@x.com", role="admin", code="anything")[1]
        self.assertIn("Administrator sign-up is turned off", r.get_data(as_text=True))
        self.assertNotIn("a@x.com", self.roles())
        self.app.config["ADMIN_SIGNUP_CODE"] = "letmein-123"
        for i in range(5):
            self.signup(f"g{i}@x.com", role="admin", code="nope")
        r = self.signup("late@x.com", role="admin", code="letmein-123")[1]     # right code, but locked out
        self.assertIn("Too many wrong admin codes", r.get_data(as_text=True))
        self.assertNotIn("late@x.com", self.roles())

    def test_user_sees_only_own_details_and_is_blocked_from_users_pages(self):
        admin = self.sign_in(self.app.test_client(), "first@x.com")
        bob, _ = self.signup("bob@x.com", name="Bob Singh")
        eve, _ = self.signup("eve@x.com", name="Eve Rao")
        btok, etok = self.sign_in(bob, "bob@x.com"), self.sign_in(eve, "eve@x.com")
        bob.post("/api/logs", headers={"X-CSRFToken": btok}, json=dict(self.LOG, customer_name="Bob Customer"))
        eve.post("/api/logs", headers={"X-CSRFToken": etok}, json=dict(self.LOG, customer_name="Eve Customer"))
        page = bob.get("/account").get_data(as_text=True)
        self.assertIn("Bob Singh", page)
        self.assertIn("bob@x.com", page)
        self.assertIn("Bob Customer", page)
        self.assertNotIn("Eve Customer", page)
        self.assertNotIn("eve@x.com", page)
        self.assertEqual(bob.get("/users").status_code, 403)
        self.assertEqual(bob.get("/users/1").status_code, 403)
        self.assertNotIn(">Users<", bob.get("/dashboard").get_data(as_text=True))          # no menu entry
        mine = bob.get("/logs?user=3").get_data(as_text=True)                                # someone else's id
        self.assertNotIn("Eve Customer", mine)

    def test_admin_sees_every_user_and_their_full_details(self):
        client = self.app.test_client()
        self.sign_in(client, "first@x.com")
        bob, _ = self.signup("bob@x.com", name="Bob Singh")
        eve, _ = self.signup("eve@x.com", name="Eve Rao")
        btok, etok = self.sign_in(bob, "bob@x.com"), self.sign_in(eve, "eve@x.com")
        bob.post("/api/logs", headers={"X-CSRFToken": btok}, json=dict(self.LOG, customer_name="Bob Customer"))
        eve.post("/api/logs", headers={"X-CSRFToken": etok}, json=dict(self.LOG, customer_name="Eve Customer"))
        users = client.get("/users").get_data(as_text=True)
        for text in ("Bob Singh", "Eve Rao", "bob@x.com", "eve@x.com", "Administrator"):
            self.assertIn(text, users)
        self.assertIn("Bob Singh", client.get("/users?q=bob").get_data(as_text=True))
        self.assertNotIn("Eve Rao", client.get("/users?q=bob").get_data(as_text=True))
        detail = client.get("/users/2").get_data(as_text=True)
        self.assertIn("Bob Singh", detail)
        self.assertIn("Bob Customer", detail)
        self.assertNotIn("Eve Customer", detail)
        self.assertEqual(client.get("/users/999").status_code, 404)
        only_eve = client.get("/logs?user=3").get_data(as_text=True)
        self.assertIn("Eve Customer", only_eve)
        self.assertNotIn("Bob Customer", only_eve)
        self.assertIn("Bob Customer", client.get("/logs").get_data(as_text=True))           # unfiltered: everything
        self.assertEqual(client.get("/account").status_code, 200)
        self.assertEqual(self.app.test_client().get("/users").status_code, 302)              # signed out


class AIProviderTests(Base):
    def test_provider_is_picked_from_the_key(self):
        from config import PROVIDERS, pick_provider
        self.assertEqual(pick_provider("", "sk-abc", None), "openai")
        self.assertEqual(pick_provider("", "gsk_abc", None), "groq")          # Groq key pasted in OPENAI_API_KEY
        self.assertEqual(pick_provider("", None, "anything"), "groq")         # GROQ_API_KEY alone
        self.assertEqual(pick_provider("", "sk-old", "gsk_new"), "groq")      # old OpenAI key left in .env
        self.assertEqual(pick_provider("", None, None), "openai")
        self.assertEqual(pick_provider("OpenAI", "gsk_abc", None), "openai")  # explicit choice wins
        self.assertEqual(pick_provider("groq", "sk-abc", None), "groq")
        self.assertIn("groq.com", PROVIDERS["groq"]["base_url"])

    def test_quota_message_names_the_provider_and_keeps_the_recording(self):
        import io
        self.app.config.update(OPENAI_API_KEY="gsk_test", AI_PROVIDER_NAME="Groq")
        tok = self.signed_in()
        resp = mock.Mock(status_code=429, json=lambda: {"error": {"message": "limit"}})
        with mock.patch("app.services.ai.requests.post", return_value=resp):
            r = self.api("post", "/api/transcribe", tok,
                         data={"audio": (io.BytesIO(b"x" * 3000), "r.webm")}, content_type="multipart/form-data")
        j = r.get_json()
        self.assertEqual(r.status_code, 502)
        self.assertIn("Groq says", j["message"])
        self.assertIn("The recording is saved", j["message"])
        self.assertIn("filename", j)                                          # file kept; the page still lists it


class LanguageTests(Base):
    def test_switch_and_persist_across_login_logout(self):
        self.assertIn("Sign in", self.c.get("/login").get_data(as_text=True))
        self.assertEqual(self.c.get("/lang/hi?next=/login").status_code, 302)
        page = self.c.get("/login").get_data(as_text=True)
        self.assertIn('<html lang="hi">', page)
        self.assertIn("साइन इन", page)
        self.register()
        self.login()
        dash = self.c.get("/dashboard").get_data(as_text=True)
        self.assertIn("नमस्ते, Asha", dash)
        self.assertIn("डैशबोर्ड", dash)
        tok = self.token(path="/dashboard")
        self.c.post("/logout", data=dict(csrf_token=tok))
        self.assertIn("साइन इन", self.c.get("/login").get_data(as_text=True))    # language kept
        self.c.get("/lang/en")
        self.assertIn("Sign in", self.c.get("/login").get_data(as_text=True))

    def test_invalid_language_and_open_redirect_ignored(self):
        r = self.c.get("/lang/xx?next=https://evil.com")
        self.assertTrue(r.headers["Location"].endswith("/"))
        self.assertIn("Sign in", self.c.get("/login").get_data(as_text=True))

    def test_hindi_pages_validation_and_js_strings(self):
        tok = self.signed_in()
        self.c.get("/lang/hi")
        rec = self.c.get("/record").get_data(as_text=True)
        self.assertIn("रिकॉर्डिंग शुरू करें", rec)
        raw = re.search(r'data-i18n="([^"]*)"', rec).group(1)
        shipped = json.loads(htmllib.unescape(raw))           # exactly what record.js parses
        self.assertEqual(shipped["Saving…"], "सेव हो रहा है…")
        self.assertIn("सर्वर तक नहीं पहुँच सके",
                      shipped["Could not reach the server. Check your connection and try again."])
        self.assertIn("लंबित", rec)                              # status option label
        r = self.api("post", "/api/logs", tok, json=dict(self.LOG, customer_name="", location="x" * 300))
        errs = r.get_json()["errors"]
        self.assertEqual(errs["customer_name"], "ग्राहक का नाम ज़रूरी है।")
        self.assertIn("200", errs["location"])
        self.assertIn("अक्षर", errs["location"])
        self.assertIn("कृपया चिह्नित फ़ील्ड ठीक करें", r.get_json()["message"])
        self.assertIn("नहीं मिला", self.c.get("/nope").get_data(as_text=True))
        lid = self.api("post", "/api/logs", tok, json=dict(self.LOG, visit_date="2025-03-05")).get_json()["id"]
        self.assertIn("मार्च", self.c.get(f"/logs/{lid}").get_data(as_text=True))       # Hindi month name
        self.assertIn("Follow-up Required", self.c.get("/logs/export.csv").get_data(as_text=True))  # DB stays English

    def test_every_template_string_has_a_hindi_translation(self):
        from app import i18n
        from app.translations_hi import HI
        pattern = re.compile(r'''\b_n?\(\s*(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)')(?:\s*,\s*(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)'))?''')
        files = glob.glob("app/templates/**/*.html", recursive=True) + [
            f for f in glob.glob("app/**/*.py", recursive=True) if not f.endswith(("i18n.py", "translations_hi.py"))]
        missing = set()
        for path in files:
            with open(path, encoding="utf8") as fh:
                text = fh.read()
            for m in pattern.finditer(text):
                for key in (m.group(1) or m.group(2), m.group(3) or m.group(4)):
                    if key and key not in HI:
                        missing.add((path, key))
        missing |= {("js", k) for k in i18n.JS_KEYS if k not in HI}
        self.assertEqual(missing, set())
        for en, hi in HI.items():      # placeholders must match between English and Hindi
            self.assertEqual(sorted(re.findall(r"\{\w+\}", en)), sorted(re.findall(r"\{\w+\}", hi)), en)


if __name__ == "__main__":
    unittest.main()
