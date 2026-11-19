// The version line at the bottom of the sidebar, from web/version.json.
fetch('/version.json',{cache:'no-cache'}).then(r=>r.ok?r.json():null).then(j=>{if(j&&j.version)document.getElementById('akVer').textContent='v'+j.version;}).catch(()=>{});
