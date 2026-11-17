import io
import os
import re
import tempfile
import unittest
from datetime import date, timedelta
from unittest import mock

from app import create_app
from app.services import ai


def make_app(**extra):
    tmp = tempfile.mkdtemp()
    cfg = dict(TESTING=True, DB_BACKEND="sqlite", SQLITE_PATH=os.path.join(tmp, "t.db"),
               UPLOAD_DIR=os.path.join(tmp, "up"), OPENAI_API_KEY=None, SECRET_KEY="x")
    cfg.update(extra)
    app = create_app(cfg)
    from app import db
    db.init_schema(app.config)
    return app


class Base(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.c = self.app.test_client()

    def token(self, client=None, path="/login"):
        client = client or self.c
        html = client.get(path).get_data(as_text=True)
        return re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)

    def register(self, client=None, email="a@x.com", name="Asha Rao", pw="password1"):
        client = client or self.c
        t = self.token(client, "/register")
        return client.post("/register", data=dict(csrf_token=t, name=name, email=email,
                                                  password=pw, confirm=pw))

    def login(self, client=None, email="a@x.com", pw="password1"):
        client = client or self.c
        t = self.token(client)
        return client.post("/login", data=dict(csrf_token=t, email=email, password=pw))

    def signed_in(self, **kw):
        self.register(**kw)
        self.login(**{k: v for k, v in kw.items() if k in ("email",)})
        return self.token(path="/dashboard")

    def api(self, method, url, tok, **kw):
        return getattr(self.c, method)(url, headers={"X-CSRFToken": tok}, **kw)

    LOG = dict(customer_name="Sharma Traders", location="Noida", contact_person="Mr Sharma",
               visit_date=date.today().isoformat(), observations="Stock is low",
               issues="Late delivery", follow_up="Send quote", follow_up_date="", status="Follow-up Required",
               transcript="hello")


class AuthTests(Base):
    def test_redirects_when_logged_out(self):
        r = self.c.get("/dashboard")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.headers["Location"])
        self.assertEqual(self.c.get("/api/status").status_code, 401)

    def test_register_login_logout(self):
        self.assertEqual(self.register().status_code, 302)
        bad = self.login(pw="wrongwrong")
        self.assertIn("incorrect", bad.get_data(as_text=True))
        self.assertEqual(self.login().status_code, 302)
        self.assertEqual(self.c.get("/dashboard").status_code, 200)
        t = self.token(path="/dashboard")
        self.c.post("/logout", data=dict(csrf_token=t))
        self.assertEqual(self.c.get("/dashboard").status_code, 302)

    def test_csrf_required(self):
        self.assertEqual(self.c.post("/login", data=dict(email="a", password="b")).status_code, 400)

    def test_first_user_admin_second_user_not(self):
        self.register()
        self.register(email="b@x.com", name="Bob")
        from app import db
        with self.app.app_context():
            roles = {r["email"]: r["role"] for r in db.get_db().all("SELECT email, role FROM users")}
        self.assertEqual(roles, {"a@x.com": "admin", "b@x.com": "user"})

    def test_register_validation_and_duplicate(self):
        self.register()
        r = self.register()
        self.assertIn("already exists", r.get_data(as_text=True))
        r = self.register(email="c@x.com", pw="short")
        self.assertIn("at least 8", r.get_data(as_text=True))

    def test_open_redirect_blocked(self):
        self.register()
        t = self.token()
        r = self.c.post("/login", data=dict(csrf_token=t, email="a@x.com", password="password1",
                                            next="https://evil.com"))
        self.assertTrue(r.headers["Location"].endswith("/dashboard"))

    def test_lockout(self):
        self.register()
        for _ in range(5):
            self.login(pw="nopenope")
        r = self.login()  # correct password but locked
        self.assertIn("Too many", r.get_data(as_text=True))
        from app.blueprints import auth
        auth._FAILED.clear()


class LogTests(Base):
    def create(self, tok, **over):
        body = dict(self.LOG, **over)
        return self.api("post", "/api/logs", tok, json=body)

    def test_create_list_detail_edit_delete(self):
        tok = self.signed_in()
        r = self.create(tok)
        self.assertEqual(r.status_code, 201)
        lid = r.get_json()["id"]
        page = self.c.get("/logs").get_data(as_text=True)
        self.assertIn("Sharma Traders", page)
        self.assertEqual(self.c.get(f"/logs/{lid}").status_code, 200)
        e = self.c.post(f"/logs/{lid}/edit", data=dict(csrf_token=tok, **dict(self.LOG, location="Delhi")))
        self.assertEqual(e.status_code, 302)
        self.assertIn("Delhi", self.c.get(f"/logs/{lid}").get_data(as_text=True))
        bad = self.c.post(f"/logs/{lid}/edit", data=dict(csrf_token=tok, **dict(self.LOG, customer_name="")))
        self.assertEqual(bad.status_code, 422)
        d = self.c.post(f"/logs/{lid}/delete", data=dict(csrf_token=tok))
        self.assertEqual(d.status_code, 302)
        self.assertEqual(self.c.get(f"/logs/{lid}").status_code, 404)

    def test_validation_errors(self):
        tok = self.signed_in()
        r = self.create(tok, customer_name="", status="Bogus", visit_date="31-02-2024")
        self.assertEqual(r.status_code, 422)
        errs = r.get_json()["errors"]
        self.assertEqual(set(errs), {"customer_name", "status", "visit_date"})

    def test_search_filter_pagination_and_wildcards(self):
        tok = self.signed_in()
        for i in range(17):
            self.create(tok, customer_name=f"Cust {i:02d}", status="Pending")
        self.create(tok, customer_name="100% Steel", status="Completed")
        p1 = self.c.get("/logs").get_data(as_text=True)
        self.assertIn("page 1 of 2", p1)
        self.assertIn("page 2 of 2", self.c.get("/logs?page=2").get_data(as_text=True))
        self.assertIn("100% Steel", self.c.get("/logs?q=100%25").get_data(as_text=True))
        self.assertNotIn("Cust 01", self.c.get("/logs?q=100%25").get_data(as_text=True))
        self.assertIn("1 visit log ", self.c.get("/logs?status=Completed").get_data(as_text=True))
        self.assertIn("No visits match", self.c.get("/logs?q=zzz").get_data(as_text=True))
        self.assertEqual(self.c.get("/logs?page=abc").status_code, 200)

    def test_users_cannot_see_each_others_logs_but_admin_can(self):
        tok = self.signed_in()  # admin (first user)
        other = self.app.test_client()
        self.register(other, email="b@x.com", name="Bob")
        self.login(other, email="b@x.com")
        otok = self.token(other, "/dashboard")
        lid = other.post("/api/logs", headers={"X-CSRFToken": otok}, json=self.LOG).get_json()["id"]
        # a third user is blocked
        third = self.app.test_client()
        self.register(third, email="c@x.com", name="Cy")
        self.login(third, email="c@x.com")
        self.assertEqual(third.get(f"/logs/{lid}").status_code, 404)
        ttok = self.token(third, "/dashboard")
        self.assertEqual(third.post(f"/logs/{lid}/delete", data=dict(csrf_token=ttok)).status_code, 404)
        # admin sees it
        self.assertEqual(self.c.get(f"/logs/{lid}").status_code, 200)

    def test_export_csv_injection_safe(self):
        tok = self.signed_in()
        self.create(tok, customer_name="=cmd|calc", observations="@x")
        r = self.c.get("/logs/export.csv")
        text = r.get_data(as_text=True)
        self.assertEqual(r.mimetype, "text/csv")
        self.assertIn("'=cmd|calc", text)
        self.assertIn("'@x", text)

    def test_dashboard_and_customers(self):
        tok = self.signed_in()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        self.create(tok, follow_up_date=yesterday)
        self.create(tok, customer_name="sharma traders", status="Completed")
        html = self.c.get("/dashboard").get_data(as_text=True)
        self.assertIn("1d late", html)
        from app.repositories import logs
        with self.app.test_request_context():
            from app import db
            user = db.get_db().one("SELECT * FROM users")
            s = logs.dashboard_stats(user)
            self.assertEqual((s["total"], s["customers"], s["need_followup"], s["completed"]), (2, 1, 1, 1))
            c = logs.customers_summary(user)
            self.assertEqual(c[0]["visits"], 2)
        self.assertIn("Sharma Traders", self.c.get("/customers").get_data(as_text=True))
        self.assertEqual(self.c.get("/record").status_code, 200)


class ApiTests(Base):
    def audio(self, name="r.webm", size=2000):
        return dict(audio=(io.BytesIO(b"x" * size), name))

    def test_transcribe_without_key_saves_audio(self):
        tok = self.signed_in()
        r = self.api("post", "/api/transcribe", tok, data=self.audio(), content_type="multipart/form-data")
        j = r.get_json()
        self.assertTrue(j["success"])
        self.assertFalse(j["ai_configured"])
        self.assertRegex(j["filename"], r"^[0-9a-f]{32}\.webm$")

    def test_transcribe_rejects_bad_input(self):
        tok = self.signed_in()
        self.assertEqual(self.api("post", "/api/transcribe", tok).status_code, 400)
        r = self.api("post", "/api/transcribe", tok, data=self.audio("x.exe"), content_type="multipart/form-data")
        self.assertEqual(r.status_code, 400)
        r = self.api("post", "/api/transcribe", tok, data=self.audio(size=10), content_type="multipart/form-data")
        self.assertIn("empty", r.get_json()["message"])

    def test_transcribe_with_ai_mocked_and_audio_attached_and_served(self):
        self.app.config["OPENAI_API_KEY"] = "sk-test"
        tok = self.signed_in()
        resp = mock.Mock(status_code=200, json=lambda: {"text": "Visited Sharma Traders in Noida"})
        with mock.patch("app.services.ai.requests.post", return_value=resp) as post:
            r = self.api("post", "/api/transcribe", tok, data=self.audio(), content_type="multipart/form-data")
        self.assertEqual(r.get_json()["transcript"], "Visited Sharma Traders in Noida")
        self.assertIn("/audio/transcriptions", post.call_args[0][0] if post.call_args[0] else post.call_args.args[0])
        fn = r.get_json()["filename"]
        lid = self.api("post", "/api/logs", tok, json=dict(self.LOG, audio_file=fn)).get_json()["id"]
        a = self.c.get(f"/logs/{lid}/audio")
        self.assertEqual(a.status_code, 200)
        self.assertEqual(len(a.get_data()), 2000)
        # path traversal / unknown names are ignored
        lid2 = self.api("post", "/api/logs", tok, json=dict(self.LOG, audio_file="../../etc/passwd")).get_json()["id"]
        self.assertEqual(self.c.get(f"/logs/{lid2}/audio").status_code, 404)
        # deleting the log deletes the file
        self.c.post(f"/logs/{lid}/delete", data=dict(csrf_token=tok))
        self.assertEqual(self.c.get(f"/logs/{lid}/audio").status_code, 404)

    def test_transcribe_ai_error_is_reported(self):
        self.app.config["OPENAI_API_KEY"] = "sk-bad"
        tok = self.signed_in()
        resp = mock.Mock(status_code=401, json=lambda: {"error": {"message": "bad key"}})
        with mock.patch("app.services.ai.requests.post", return_value=resp):
            r = self.api("post", "/api/transcribe", tok, data=self.audio(), content_type="multipart/form-data")
        self.assertEqual(r.status_code, 502)
        self.assertIn("API key", r.get_json()["message"])
        self.assertIn("filename", r.get_json())

    def test_extract_ai_and_fallback(self):
        self.app.config["OPENAI_API_KEY"] = "sk-test"
        tok = self.signed_in()
        content = '```json\n{"customer_name":"Acme","status":"completed","visit_date":"2024-13-45","junk":"x"}\n```'
        resp = mock.Mock(status_code=200, json=lambda: {"choices": [{"message": {"content": content}}]})
        with mock.patch("app.services.ai.requests.post", return_value=resp):
            j = self.api("post", "/api/extract", tok, json={"transcript": "hi"}).get_json()
        self.assertEqual(j["method"], "ai")
        self.assertEqual(j["fields"]["customer_name"], "Acme")
        self.assertEqual(j["fields"]["status"], "Completed")
        self.assertEqual(j["fields"]["visit_date"], "")
        self.assertNotIn("junk", j["fields"])
        import requests
        with mock.patch("app.services.ai.requests.post", side_effect=requests.ConnectionError()):
            j = self.api("post", "/api/extract", tok, json={"transcript": "Visited Acme Corp in Pune."}).get_json()
        self.assertEqual(j["method"], "basic")
        self.assertIn("basic extraction", j["warning"])
        self.assertEqual(self.api("post", "/api/extract", tok, json={"transcript": " "}).status_code, 400)


class BasicExtractTests(unittest.TestCase):
    def test_typical_transcript(self):
        today = date(2025, 3, 5)  # a Wednesday
        text = ("Today I visited Sharma Traders in Noida. I met Mr Gupta. The freezer is not working "
                "and there is a delay in payment. Need to call back next week to send a quotation.")
        f = ai.basic_extract(text, today)
        self.assertEqual(f["customer_name"], "Sharma Traders")
        self.assertEqual(f["location"], "Noida")
        self.assertEqual(f["contact_person"], "Gupta")
        self.assertEqual(f["visit_date"], "2025-03-05")
        self.assertEqual(f["follow_up_date"], "2025-03-12")
        self.assertEqual(f["status"], "Follow-up Required")
        self.assertIn("not working", f["issues"])

    def test_empty_and_completed(self):
        self.assertEqual(ai.basic_extract("")["status"], "")
        f = ai.basic_extract("Met Mr Rao at Acme Ltd. Installation done.", date(2025, 3, 5))
        self.assertEqual(f["status"], "Completed")


if __name__ == "__main__":
    unittest.main()
