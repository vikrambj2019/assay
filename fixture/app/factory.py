"""Synthetic demo Flask app for the assay regression evaluation fixture.

``create_app(bugs=[...])`` returns a Flask application configured with zero
or more deliberate bugs enabled.  With an empty *bugs* list the app behaves
correctly on all flows; each bug ID enables one specific regression.

Bug IDs are stable — they never change, so evaluation results from different
runs can be compared.

Supported bug IDs
-----------------
BUG-001  login_wrong_redirect    POST /login → /home instead of /dashboard
BUG-002  toast_missing           POST /records → no "Record saved" banner
BUG-003  edit_not_persisted      POST /records/<id>/edit → value reverts on reload
BUG-004  validation_bypassed     POST /records (empty name) → accepted silently
BUG-005  detail_link_broken      /records list links to /records-detail/<id> (404)

The app also contains a flow whose requirement is deliberately ambiguous
(AMBIGUOUS-001) — the "Total" calculation is displayed but the spec does not
define what "correct" means, so a deterministic assertion check cannot pass.

Usage (test client)::

    app = create_app()                          # clean
    app = create_app(bugs=["BUG-001"])          # login redirect broken

Usage (real browser via subprocess)::

    flask --app fixture.app.factory:create_app run --port 5173
"""

from __future__ import annotations

from flask import Flask, redirect, render_template_string, request, session

# ── Fixture user store ────────────────────────────────────────────────────────

FIXTURE_USERNAME = "testuser"
FIXTURE_PASSWORD = "password123"
_USERS: dict[str, str] = {FIXTURE_USERNAME: FIXTURE_PASSWORD}

# ── HTML templates (inline — no external template files required) ─────────────

_LOGIN_HTML = """\
<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Login — Assay Fixture</title></head>
<body>
<h1>Sign in</h1>
{% if error %}<p id="login-error">{{ error }}</p>{% endif %}
<form method="post" action="/login">
  <label>Username <input type="text" name="username" id="username"></label><br>
  <label>Password <input type="password" name="password" id="password"></label><br>
  <button type="submit" id="submit-login">Sign in</button>
</form>
</body></html>
"""

_DASHBOARD_HTML = """\
<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Dashboard — Assay Fixture</title></head>
<body>
<h1>Dashboard</h1>
<p id="welcome-msg">Welcome, {{ user }}!</p>
<nav><a href="/records" id="nav-records">Records</a></nav>
</body></html>
"""

_RECORDS_HTML = """\
<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Records — Assay Fixture</title></head>
<body>
<h1>Records</h1>
{% if saved %}<p id="toast-saved">Record saved</p>{% endif %}
{% if error %}<p id="validation-error">{{ error }}</p>{% endif %}
<ul id="record-list">
{% for r in records %}
  <li>
    <a href="{{ r['detail_url'] }}" id="link-record-{{ r['id'] }}">{{ r['name'] }}</a>
    &mdash; qty: {{ r['quantity'] }}, ${{ "%.2f"|format(r['price']) }}
  </li>
{% endfor %}
</ul>
<h2>Add record</h2>
<form method="post" action="/records" id="form-add-record">
  <label>Name <input type="text" name="name" id="input-name"
    value="{{ form_name }}"></label><br>
  <label>Quantity <input type="number" name="quantity" id="input-qty"
    value="{{ form_qty }}"></label><br>
  <label>Price <input type="number" step="0.01" name="price" id="input-price"
    value="{{ form_price }}"></label><br>
  <button type="submit" id="btn-add">Add</button>
</form>
</body></html>
"""

_DETAIL_HTML = """\
<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{{ record['name'] }} — Assay Fixture</title></head>
<body>
<h1 id="record-name">{{ record['name'] }}</h1>
<p>Quantity: <span id="record-qty">{{ record['quantity'] }}</span></p>
<p>Price: $<span id="record-price">{{ "%.2f"|format(record['price']) }}</span></p>
<p>Total: $<span id="record-total">{{ "%.2f"|format(record['quantity'] * record['price']) }}</span></p>
<form method="post" action="/records/{{ record['id'] }}/edit" id="form-edit">
  <label>Name <input type="text" name="name" id="edit-name"
    value="{{ record['name'] }}"></label><br>
  <button type="submit" id="btn-save">Save</button>
</form>
<a href="/records" id="nav-back">Back to records</a>
</body></html>
"""

_NOT_FOUND_HTML = """\
<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Not Found</title></head>
<body><h1>Not Found</h1><p>The requested page does not exist.</p></body></html>
"""


# ── App factory ───────────────────────────────────────────────────────────────

def create_app(bugs: list[str] | None = None) -> Flask:
    """Create a fresh Flask app instance.

    Args:
        bugs: List of bug IDs to enable.  ``None`` or ``[]`` = clean app.

    Returns:
        Configured Flask application.  Each call returns a new instance with
        an independent in-memory data store so tests are isolated.
    """
    app = Flask(__name__)
    app.secret_key = "assay-fixture-dev-key-not-for-production"
    active_bugs: frozenset[str] = frozenset(bugs or [])

    # Per-instance in-memory record store.
    _records: dict[int, dict] = {
        1: {"id": 1, "name": "Widget A", "quantity": 5, "price": 9.99},
    }
    _next_id: list[int] = [2]

    # ── Routes ────────────────────────────────────────────────────────────────

    @app.route("/")
    def index():
        return redirect("/login")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error = ""
        if request.method == "POST":
            username = request.form.get("username", "")
            password = request.form.get("password", "")
            if _USERS.get(username) == password:
                session["user"] = username
                # BUG-001: redirect to /home (does not exist) instead of /dashboard
                target = "/home" if "BUG-001" in active_bugs else "/dashboard"
                return redirect(target)
            error = "Invalid credentials"
        return render_template_string(_LOGIN_HTML, error=error)

    @app.route("/dashboard")
    def dashboard():
        user = session.get("user", "guest")
        return render_template_string(_DASHBOARD_HTML, user=user)

    @app.route("/records", methods=["GET", "POST"])
    def records():
        error = ""
        form_name = form_qty = form_price = ""

        if request.method == "POST":
            name = request.form.get("name", "").strip()
            quantity = request.form.get("quantity", "").strip()
            price_str = request.form.get("price", "").strip()

            # BUG-004: skip validation — accept empty name silently
            if not name and "BUG-004" not in active_bugs:
                error = "Name is required"
                form_name = name
                form_qty = quantity
                form_price = price_str
            else:
                rid = _next_id[0]
                _next_id[0] += 1
                try:
                    q = int(quantity) if quantity else 1
                except ValueError:
                    q = 1
                try:
                    p = float(price_str) if price_str else 0.0
                except ValueError:
                    p = 0.0
                _records[rid] = {
                    "id": rid,
                    "name": name if name else "Unnamed",
                    "quantity": q,
                    "price": p,
                }
                # BUG-002: omit ?saved=1 so the toast does not appear
                return redirect("/records" if "BUG-002" in active_bugs else "/records?saved=1")

        saved = request.args.get("saved") == "1"
        recs_with_url = []
        for r in _records.values():
            # BUG-005: link points to /records-detail/<id> (returns 404)
            detail_url = (
                f"/records-detail/{r['id']}"
                if "BUG-005" in active_bugs
                else f"/records/{r['id']}"
            )
            recs_with_url.append({**r, "detail_url": detail_url})

        return render_template_string(
            _RECORDS_HTML,
            records=recs_with_url,
            saved=saved,
            error=error,
            form_name=form_name,
            form_qty=form_qty,
            form_price=form_price,
        )

    @app.route("/records/<int:rid>")
    def record_detail(rid: int):
        r = _records.get(rid)
        if r is None:
            return render_template_string(_NOT_FOUND_HTML), 404
        return render_template_string(_DETAIL_HTML, record=r)

    @app.route("/records/<int:rid>/edit", methods=["POST"])
    def record_edit(rid: int):
        r = _records.get(rid)
        if r is None:
            return render_template_string(_NOT_FOUND_HTML), 404
        new_name = request.form.get("name", "").strip()
        if new_name and "BUG-003" not in active_bugs:
            # BUG-003: silently discard the edit — value reverts on next load
            r["name"] = new_name
        return redirect(f"/records/{rid}")

    @app.errorhandler(404)
    def not_found(_err):
        return render_template_string(_NOT_FOUND_HTML), 404

    return app
