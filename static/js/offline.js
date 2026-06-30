// GPS capture
const getLocBtn = document.getElementById('getLocationBtn');
if (getLocBtn) {
  getLocBtn.addEventListener('click', () => {
    const locStatus = document.getElementById('locStatus');
    if (!navigator.geolocation) {
      locStatus.textContent = 'Geolocation not supported';
      return;
    }
    locStatus.textContent = 'Capturing...';
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        document.getElementById('latitude').value = pos.coords.latitude.toFixed(6);
        document.getElementById('longitude').value = pos.coords.longitude.toFixed(6);
        locStatus.textContent = '✅ Captured';
      },
      (err) => { locStatus.textContent = 'Error: ' + err.message; },
      { enableHighAccuracy: true, timeout: 15000 }
    );
  });
}

// Add family member rows
const addFamilyBtn = document.getElementById('addFamilyRow');
if (addFamilyBtn) {
  addFamilyBtn.addEventListener('click', () => {
    const div = document.createElement('div');
    div.className = 'family-row';
    div.innerHTML = `<input type="text" name="family_name[]" placeholder="Name / பெயர்">
                      <input type="text" name="family_relation[]" placeholder="Relation / உறவு முறை">`;
    document.getElementById('familyRows').appendChild(div);
  });
}

// Online/offline status indicator + auto-trigger sync when device comes online
function updateNetStatus() {
  fetch('/api/status').then(r => r.json()).then(data => {
    const el = document.getElementById('netStatus');
    if (!el) return;
    if (data.online) {
      el.textContent = `Online · ${data.pending_sync} pending sync`;
      el.className = 'status online';
    } else {
      el.textContent = `Offline · ${data.pending_sync} pending sync`;
      el.className = 'status offline';
    }
  }).catch(() => {
    const el = document.getElementById('netStatus');
    if (el) { el.textContent = 'Offline (server unreachable)'; el.className = 'status offline'; }
  });
}
updateNetStatus();
setInterval(updateNetStatus, 15000);
