const videoStream = document.getElementById('videoStream');
const startBtn = document.getElementById('startBtn');
const stopBtn = document.getElementById('stopBtn');
const phaseText = document.getElementById('phase_text');
const refreshBtn = document.getElementById('refreshBtn');
const adminBtn = document.getElementById('adminBtn');
const adminModal = document.getElementById('adminModal');
const projectModal = document.getElementById('projectModal');
const closeAdminBtn = document.getElementById('closeAdminBtn');
const projectDetailsBtn = document.getElementById('projectDetailsBtn');
const closeProjectBtn = document.getElementById('closeProjectBtn');
const adminExitBtn = document.getElementById('adminExitBtn');
const adminPassword = document.getElementById('adminPassword');
const adminStatus = document.getElementById('adminStatus');

let sessionPolling = null;
let adminExitInProgress = false;

function forceRefreshPage(e) {
    if (e) e.preventDefault();
    const separator = window.location.search ? '&' : '?';
    window.location.href = `${window.location.pathname}${window.location.search}${separator}refresh=${Date.now()}`;
}

refreshBtn.addEventListener('click', forceRefreshPage);
refreshBtn.addEventListener('touchstart', forceRefreshPage, { passive: false });

adminBtn.addEventListener('click', function() {
    showAdminModal();
});

closeAdminBtn.addEventListener('click', function() {
    hideAdminModal();
});

projectDetailsBtn.addEventListener('click', function() {
    showProjectModal();
});

closeProjectBtn.addEventListener('click', function() {
    hideProjectModal();
});

adminExitBtn.addEventListener('click', function() {
    if (adminExitInProgress) return;

    const password = (adminPassword.value || '').trim();
    if (!password) {
        setAdminStatus('Enter the admin password to continue.', true);
        return;
    }

    adminExitInProgress = true;
    adminExitBtn.disabled = true;
    setAdminStatus('Authorizing exit...');

    fetch('/admin/exit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password })
    })
    .then(async (res) => {
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
            throw new Error(data.error || 'Authorization failed.');
        }

        setAdminStatus('Exit approved. Closing Chromium and returning to desktop...', false, true);
        setTimeout(() => {
            window.location.href = '/';
        }, 900);
    })
    .catch((err) => {
        setAdminStatus(err.message || 'Exit failed.', true);
    })
    .finally(() => {
        adminExitInProgress = false;
        adminExitBtn.disabled = false;
        adminPassword.value = '';
    });
});

adminPassword.addEventListener('keydown', function(e) {
    if (e.key === 'Enter') {
        e.preventDefault();
        adminExitBtn.click();
    }
});

adminModal.addEventListener('click', function(e) {
    if (e.target === adminModal) hideAdminModal();
});

projectModal.addEventListener('click', function(e) {
    if (e.target === projectModal) hideProjectModal();
});

startBtn.addEventListener('click', function() {
    fetch('/start_session', { method: 'POST' })
    .then(res => res.json())
    .then(() => {
        document.getElementById('cam-error').style.display = 'none';
        videoStream.style.display = 'block';
        videoStream.src = `/video_feed?t=${Date.now()}`;
        phaseText.innerText = 'Position your hand. Keep nail and skin box visible.';
        document.getElementById('hb_val').innerText = '--';
        const badge = document.getElementById('status_badge');
        badge.style.opacity = '1';
        badge.innerText = 'Preparing...';
        badge.className = 'status-badge mild';

        if (sessionPolling) clearInterval(sessionPolling);
        sessionPolling = setInterval(syncSessionState, 350);
    });
});

stopBtn.addEventListener('click', function() {
    fetch('/stop_session', { method: 'POST' })
    .then(res => res.json())
    .then(() => {
        if (sessionPolling) {
            clearInterval(sessionPolling);
            sessionPolling = null;
        }
        videoStream.src = '';
        videoStream.style.display = 'none';
        document.getElementById('cam-error').style.display = 'block';
        phaseText.innerText = 'Session stopped. Press Start to begin again.';
        document.getElementById('hb_val').innerText = '--';
        const badge = document.getElementById('status_badge');
        badge.style.opacity = '1';
        badge.innerText = 'Ready';
        badge.className = 'status-badge normal';
    });
});

function syncSessionState() {
    fetch('/get_session_state')
    .then(res => res.json())
    .then(data => {
        if (data.phase === 'positioning') {
            phaseText.innerText = `Get ready: ${data.remaining}s | ${data.quality_reason}`;
            return;
        }

        if (data.phase === 'acquiring') {
            phaseText.innerText = `Analyzing: ${data.remaining}s | samples: ${data.samples}`;
            return;
        }

        if (data.phase === 'done') {
            phaseText.innerText = 'Reading complete.';
            if (data.hb > 0) {
                updateUI(data.hb, data.status);
            } else {
                const badge = document.getElementById('status_badge');
                badge.style.opacity = '1';
                badge.innerText = data.status || 'No Valid Reading';
                badge.className = 'status-badge severe';
            }
            clearInterval(sessionPolling);
            sessionPolling = null;
            videoStream.src = '';
            videoStream.style.display = 'none';
            document.getElementById('cam-error').style.display = 'block';
            return;
        }

        if (data.phase === 'idle') {
            phaseText.innerText = 'Press Start to begin camera preview';
        }
    });
}

function showAdminModal() {
    adminModal.style.display = 'flex';
    setAdminStatus('Idle');
    adminPassword.value = '';
    setTimeout(() => adminPassword.focus(), 50);
}

function hideAdminModal() {
    adminModal.style.display = 'none';
    adminPassword.value = '';
    setAdminStatus('Idle');
}

function showProjectModal() {
    projectModal.style.display = 'flex';
}

function hideProjectModal() {
    projectModal.style.display = 'none';
}

function setAdminStatus(message, isError = false, isSuccess = false) {
    adminStatus.innerText = message;
    adminStatus.className = 'admin-status';
    if (isError) adminStatus.classList.add('error');
    if (isSuccess) adminStatus.classList.add('success');
}

function updateUI(hb, status) {
    document.getElementById('hb_val').innerText = hb;
    const badge = document.getElementById('status_badge');
    badge.style.opacity = '1';
    badge.innerText = status;
    
    badge.className = "status-badge"; // reset
    if(status.includes("Severe")) badge.classList.add("severe");
    else if(status.includes("Mild") || status.includes("Moderate")) badge.classList.add("mild");
    else badge.classList.add("normal");
}