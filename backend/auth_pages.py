"""Static HTML of the login gate's pages (moved out of backend/auth.py, which builds them)."""


LOGIN_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>AgentKennel — login</title>
<link rel="icon" href="/favicon.svg" type="image/svg+xml"><link rel="icon" href="/favicon.ico" sizes="32x32"><link rel="apple-touch-icon" href="/apple-touch-icon.png">
<style>
*{box-sizing:border-box} html,body{height:100%}
body{margin:0;display:flex;align-items:center;justify-content:center;background:#0d1117;
  color:#e6edf3;font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}
.card{width:min(92vw,340px);background:#161b22;border:1px solid #30363d;border-radius:14px;
  padding:28px 26px;box-shadow:0 10px 40px rgba(0,0,0,.4)}
h1{margin:0 0 4px;font-size:19px} .sub{margin:0 0 20px;color:#8b949e;font-size:13px}
label{display:block;font-size:12px;color:#8b949e;margin:12px 0 5px}
input{width:100%;padding:10px 12px;border-radius:8px;border:1px solid #30363d;background:#0d1117;
  color:#e6edf3;font-size:15px;outline:none}
input:focus{border-color:#388bfd;box-shadow:0 0 0 3px rgba(56,139,253,.25)}
button{width:100%;margin-top:18px;padding:11px;border:0;border-radius:8px;background:#238636;
  color:#fff;font-size:15px;font-weight:600;cursor:pointer}
button:hover{background:#2ea043}
.err{margin:14px 0 0;color:#ff7b72;font-size:13px;text-align:center}
.forgot{margin:16px 0 0;font-size:12px;color:#8b949e} .forgot summary{cursor:pointer;text-align:center}
.forgot p{margin:8px 0 4px} .forgot code{display:block;padding:6px 8px;border-radius:6px;background:#0d1117;
  border:1px solid #30363d;color:#e6edf3;font-size:11.5px;overflow-wrap:anywhere}
</style>__BREAKOUT__</head><body>
<form class="card" method="POST" action="/login">
  <h1>🐾 AgentKennel</h1><p class="sub">Sign in to open the panel</p>
  __USER__<label for="p">Password</label>
  <input id="p" name="pass" type="password" autocomplete="current-password"__PFOCUS__>
  <button type="submit">Sign in</button>
  __ERR__
  <details class="forgot"><summary>Forgot your password?</summary>
    <p>On the server, run this. It prints a new one-time link to set a new password; the old
    one and every signed-in browser stop working.</p>
    <code>~/agentkennel/install.sh --reset-password</code>
    <p>Docker, in the AgentKennel folder:</p>
    <code>docker compose exec agentkennel python3 status_server.py --reset-password</code>
  </details>
</form></body></html>"""


_LOGIN_USER_FIELD = ('<label for="u">Username</label>\n'
                     '  <input id="u" name="user" autocomplete="username" autofocus>\n  ')


# A framed sign-in page takes the whole tab: after an expired session the terminal iframe
# reloads into /login, and logging in THERE nested a second dashboard inside the pane.
_FRAME_BREAKOUT = "<script>if(window.top!==window.self)window.top.location.replace('/login')</script>"


_AUTH_HEAD = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
  '<meta name="viewport" content="width=device-width,initial-scale=1"><title>AgentKennel</title>'
  '<link rel="icon" href="/favicon.svg" type="image/svg+xml"><link rel="icon" href="/favicon.ico" sizes="32x32"><link rel="apple-touch-icon" href="/apple-touch-icon.png"><style>'
  '*{box-sizing:border-box}html,body{height:100%}'
  'body{margin:0;display:flex;align-items:center;justify-content:center;background:#0d1117;'
  'color:#e6edf3;font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}'
  '.card{width:min(92vw,340px);background:#161b22;border:1px solid #30363d;border-radius:14px;'
  'padding:28px 26px;box-shadow:0 10px 40px rgba(0,0,0,.4)}'
  'h1{margin:0 0 4px;font-size:19px}.sub{margin:0 0 20px;color:#8b949e;font-size:13px}'
  'label{display:block;font-size:12px;color:#8b949e;margin:12px 0 5px}'
  'input{width:100%;padding:10px 12px;border-radius:8px;border:1px solid #30363d;background:#0d1117;'
  'color:#e6edf3;font-size:15px;outline:none}'
  'input:focus{border-color:#388bfd;box-shadow:0 0 0 3px rgba(56,139,253,.25)}'
  'button{width:100%;margin-top:18px;padding:11px;border:0;border-radius:8px;background:#238636;'
  'color:#fff;font-size:15px;font-weight:600;cursor:pointer}button:hover{background:#2ea043}'
  '.err{margin:14px 0 0;color:#ff7b72;font-size:13px;text-align:center}'
  '.ok{margin:14px 0 0;color:#3fb950;font-size:13px;text-align:center}'
  'a{color:#58a6ff}</style>' + _FRAME_BREAKOUT + '</head><body>')
