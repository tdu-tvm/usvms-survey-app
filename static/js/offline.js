// District -> ULB lists (verified official data only).
// Add more districts here as you confirm official ULB lists for each.
// Any district NOT listed here automatically falls back to free-text entry.
const ULB_DATA = {
  "Tirunelveli / திருநெல்வேலி": {
    "Corporation": ["Tirunelveli Municipal Corporation"],
    "Municipality": ["Ambasamudram", "Vickramasingapuram", "Kalakad"],
    "Town Panchayat": [
      "Cheranmahadevi", "Eruvadi", "Gopalasamudram", "Kallidaikurichi",
      "Manimutharu", "Melacheval", "Moolakaraipatti", "Mukkudal",
      "Nanguneri", "Naranammalpuram", "Panagudi", "Pathamadai",
      "Sankarnagar", "Thirukkurungudi", "Thisayanvilai", "Veeravanallur",
      "Valliyur"
    ]
  },
  "Chengalpattu / செங்கல்பட்டு": {
    "Corporation": ["Tambaram Municipal Corporation"],
    "Municipality": ["Chengalpattu", "Madurantakam", "Maraimalai Nagar", "Nandivaram-Guduvancheri"],
    "Town Panchayat": ["Acharapakkam", "Edakazhinadu", "Karunkuzhi", "Mamallapuram", "Thirukazhukundram", "Thiruporur"]
  },
  "Kanchipuram / காஞ்சிபுரம்": {
    "Corporation": ["Kancheepuram Municipal Corporation"],
    "Municipality": ["Kundrathur", "Mangadu"],
    "Town Panchayat": ["Sriperumbudur", "Uthiramerur", "Walajabad"]
  }
};

function updateUlbOptions() {
  const district = document.getElementById('districtSelect').value;
  const ulbSelect = document.getElementById('ulbSelect');
  const ulbText = document.getElementById('ulbText');
  const data = ULB_DATA[district];

  if (data) {
    // populate dropdown, hide text box
    ulbSelect.innerHTML = '<option value="">-- Select --</option>';
    Object.keys(data).forEach(group => {
      const optgroup = document.createElement('optgroup');
      optgroup.label = group;
      data[group].forEach(name => {
        const opt = document.createElement('option');
        opt.textContent = name;
        optgroup.appendChild(opt);
      });
      ulbSelect.appendChild(optgroup);
    });
    ulbSelect.style.display = 'block';
    ulbSelect.disabled = false;
    ulbText.style.display = 'none';
    ulbText.disabled = true;
  } else {
    // no verified data for this district yet -> free text fallback
    ulbSelect.style.display = 'none';
    ulbSelect.disabled = true;
    ulbText.style.display = 'block';
    ulbText.disabled = false;
  }
}
document.addEventListener('DOMContentLoaded', updateUlbOptions);


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
