const videoStream = document.getElementById('videoStream');
const startBtn = document.getElementById('startBtn');
const stopBtn = document.getElementById('stopBtn');
const phaseText = document.getElementById('phase_text');
<<<<<<< HEAD
const qualityScoreText = document.getElementById('qualityScoreText');
const qualityBarFill = document.getElementById('qualityBarFill');
const qualityTipText = document.getElementById('qualityTipText');
=======
>>>>>>> a2e288f106bbc0ca2d7d6dbd75fdf89172035065
const refreshBtn = document.getElementById('refreshBtn');
const adminBtn = document.getElementById('adminBtn');
const adminModal = document.getElementById('adminModal');
const projectModal = document.getElementById('projectModal');
const closeAdminBtn = document.getElementById('closeAdminBtn');
const projectDetailsBtn = document.getElementById('projectDetailsBtn');
const closeProjectBtn = document.getElementById('closeProjectBtn');
const adminExitBtn = document.getElementById('adminExitBtn');
<<<<<<< HEAD
const exportRecordsBtn = document.getElementById('exportRecordsBtn');
const resetDatabaseBtn = document.getElementById('resetDatabaseBtn');
const adminPassword = document.getElementById('adminPassword');
const adminStatus = document.getElementById('adminStatus');
const adminActionPanel = document.getElementById('adminActionPanel');
const adminAuthPanel = document.getElementById('adminAuthPanel');
const adminAuthConfirmBtn = document.getElementById('adminAuthConfirmBtn');
const adminAuthCancelBtn = document.getElementById('adminAuthCancelBtn');
const saveReadingBtn = document.getElementById('saveReadingBtn');
const saveReadingModal = document.getElementById('saveReadingModal');
const saveReadingForm = document.getElementById('saveReadingForm');
const cancelSaveReadingBtn = document.getElementById('cancelSaveReadingBtn');
const submitSaveReadingBtn = document.getElementById('submitSaveReadingBtn');
const saveFormStatus = document.getElementById('saveFormStatus');
const patientNameInput = document.getElementById('patientName');
const measurementTimeInput = document.getElementById('measurementTime');
const predictedHbInput = document.getElementById('predictedHb');
const predictedStatusInput = document.getElementById('predictedStatus');
const keyboardDock = document.getElementById('keyboardDock');
const virtualKeyboard = document.getElementById('virtualKeyboard');

let sessionPolling = null;
let adminExitInProgress = false;
let saveReadingInProgress = false;
let latestReading = { hb: null, status: null };
let activeKeyboardInput = null;
let pendingAdminAction = null;
let keyboardShift = false;

const KEYBOARD_ROWS = [
    ['1', '2', '3', '4', '5', '6', '7', '8', '9', '0'],
    ['Q', 'W', 'E', 'R', 'T', 'Y', 'U', 'I', 'O', 'P'],
    ['A', 'S', 'D', 'F', 'G', 'H', 'J', 'K', 'L'],
    ['Z', 'X', 'C', 'V', 'B', 'N', 'M', '-'],
    ['Shift', '.', 'Space', 'Backspace', 'Enter', 'Clear', 'Hide']
];

initializeVirtualKeyboard();
initializeCustomInputMode();

document.addEventListener('selectstart', function(e) {
    const target = e.target;
    const allowSelection = target && (
        target.tagName === 'INPUT' ||
        target.tagName === 'TEXTAREA' ||
        target.isContentEditable
    );

    if (!allowSelection) {
        e.preventDefault();
    }
});
=======
const adminPassword = document.getElementById('adminPassword');
const adminStatus = document.getElementById('adminStatus');

let sessionPolling = null;
let adminExitInProgress = false;
>>>>>>> a2e288f106bbc0ca2d7d6dbd75fdf89172035065

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
<<<<<<< HEAD
    requestAdminAction('exit');
});

exportRecordsBtn.addEventListener('click', function() {
    requestAdminAction('export');
});

resetDatabaseBtn.addEventListener('click', function() {
    requestAdminAction('reset');
});

adminAuthConfirmBtn.addEventListener('click', function() {
    runPendingAdminAction();
});

adminAuthCancelBtn.addEventListener('click', function() {
    showAdminActionPanel();
    hideAdminAuthPanel();
    setAdminStatus('Idle');
});

adminPassword.addEventListener('keydown', function(e) {
    if (e.key === 'Enter') {
        e.preventDefault();
    }
});

adminPassword.addEventListener('keyup', function(e) {
    if (e.key !== 'Enter') return;
    if (e.isComposing) return;
    if (adminAuthPanel.style.display === 'none') return;
    runPendingAdminAction();
});

adminModal.addEventListener('click', function(e) {
    if (e.target === adminModal) hideAdminModal();
});

projectModal.addEventListener('click', function(e) {
    if (e.target === projectModal) hideProjectModal();
});

startBtn.addEventListener('click', function() {
    fetch('/start_session', { method: 'POST' })
    .then(async (res) => {
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
            throw new Error(data.error || 'Failed to start camera session.');
        }
        return data;
    })
    .then(() => {
        hideSaveReadingUI();
        document.getElementById('cam-error').style.display = 'none';
        videoStream.style.display = 'block';
        videoStream.src = `/video_feed?t=${Date.now()}`;
        phaseText.innerText = 'Position your hand. Keep nail and skin box visible.';
        phaseText.style.color = '#0ea5e9';
        document.getElementById('hb_val').innerText = '--';
        const badge = document.getElementById('status_badge');
        badge.style.opacity = '1';
        badge.innerText = 'Preparing...';
        badge.className = 'status-badge large-badge mild';

        if (sessionPolling) clearInterval(sessionPolling);
        sessionPolling = setInterval(syncSessionState, 350);
    })
    .catch((err) => {
        if (sessionPolling) {
            clearInterval(sessionPolling);
            sessionPolling = null;
        }
        videoStream.src = '';
        videoStream.style.display = 'none';

        const camError = document.getElementById('cam-error');
        camError.style.display = 'block';
        camError.innerText = err.message || 'Camera failed to start.';

        setSaveStatus(err.message || 'Camera failed to start.', true);
    });
});

stopBtn.addEventListener('click', function() {
    fetch('/stop_session', { method: 'POST' })
    .then(res => res.json())
    .then(() => {
        hideSaveReadingUI();
        if (sessionPolling) {
            clearInterval(sessionPolling);
            sessionPolling = null;
        }
        videoStream.src = '';
        videoStream.style.display = 'none';
        document.getElementById('cam-error').style.display = 'block';
        phaseText.innerText = 'Session stopped. Press Start to begin again.';
        phaseText.style.color = '#0ea5e9';
        document.getElementById('hb_val').innerText = '--';
        const badge = document.getElementById('status_badge');
        badge.style.opacity = '1';
        badge.innerText = 'Ready';
        badge.className = 'status-badge large-badge normal';
    });
});

function syncSessionState() {
    fetch('/get_session_state')
    .then(res => res.json())
    .then(data => {
        updateQualityPanel(data);

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
                latestReading = { hb: data.hb, status: data.status || 'Unknown' };
                showSaveReadingUI();
            } else {
                const badge = document.getElementById('status_badge');
                badge.style.opacity = '1';
                badge.innerText = data.status || 'No Valid Reading';
                badge.className = 'status-badge large-badge severe';
                hideSaveReadingUI();
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
            phaseText.style.color = '#0ea5e9';
        }
    });
}

function updateQualityPanel(data) {
    if (!qualityScoreText || !qualityBarFill || !qualityTipText) return;

    const score = Number.isFinite(Number(data.quality_score)) ? Number(data.quality_score) : 0;
    const bounded = Math.max(0, Math.min(100, Math.round(score)));
    qualityScoreText.innerText = `${bounded}%`;
    qualityBarFill.style.width = `${bounded}%`;

    const tip = (data.quality_tip || data.quality_reason || 'Place index fingernail in the center guide area.').trim();
    qualityTipText.innerText = tip;
}

function showAdminModal() {
    adminModal.style.display = 'flex';
    setAdminStatus('Idle');
    pendingAdminAction = null;
    showAdminActionPanel();
    hideAdminAuthPanel();
    adminPassword.value = '';
    adminPassword.classList.add('vk-input');
    adminPassword.setAttribute('inputmode', 'none');
}

function hideAdminModal() {
    adminModal.style.display = 'none';
    adminPassword.value = '';
    pendingAdminAction = null;
    showAdminActionPanel();
    hideAdminAuthPanel();
    hideVirtualKeyboard();
    activeKeyboardInput = null;
    setAdminStatus('Idle');
}

function requestAdminAction(actionType) {
    pendingAdminAction = actionType;
    hideAdminActionPanel();
    showAdminAuthPanel();

    if (actionType === 'export') {
        setAdminStatus('Enter password to export records.', false, false);
    } else if (actionType === 'reset') {
        setAdminStatus('Enter password to reset database.', false, false);
    } else {
        setAdminStatus('Enter password to exit to desktop.', false, false);
    }

    setTimeout(() => adminPassword.focus(), 40);
}

function showAdminActionPanel() {
    adminActionPanel.style.display = 'block';
}

function hideAdminActionPanel() {
    adminActionPanel.style.display = 'none';
}

function showAdminAuthPanel() {
    adminAuthPanel.style.display = 'block';
}

function hideAdminAuthPanel() {
    adminAuthPanel.style.display = 'none';
    adminPassword.value = '';
}

function runPendingAdminAction() {
=======
>>>>>>> a2e288f106bbc0ca2d7d6dbd75fdf89172035065
    if (adminExitInProgress) return;

    const password = (adminPassword.value || '').trim();
    if (!password) {
        setAdminStatus('Enter the admin password to continue.', true);
        return;
    }

<<<<<<< HEAD
    if (!pendingAdminAction) {
        setAdminStatus('Select an admin action first.', true);
        return;
    }

    adminExitInProgress = true;
    adminAuthConfirmBtn.disabled = true;
    setAdminStatus('Authorizing action...');

    if (pendingAdminAction === 'export') {
        fetch('/admin/export_records', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password })
        })
        .then(async (res) => {
            const data = await res.json().catch(() => ({}));
            if (!res.ok || !data.ok) {
                throw new Error(data.error || 'Export failed.');
            }

            setAdminStatus(`Exported ${data.record_count} records to ${data.folder}/${data.filename}`, false, true);
            showAdminActionPanel();
            hideAdminAuthPanel();
        })
        .catch((err) => {
            setAdminStatus(err.message || 'Export failed.', true);
        })
        .finally(() => {
            adminExitInProgress = false;
            adminAuthConfirmBtn.disabled = false;
            adminPassword.value = '';
        });
        return;
    }

    if (pendingAdminAction === 'reset') {
        fetch('/admin/reset_database', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password })
        })
        .then(async (res) => {
            const data = await res.json().catch(() => ({}));
            if (!res.ok || !data.ok) {
                throw new Error(data.error || 'Database reset failed.');
            }

            setAdminStatus(`Database reset complete. Deleted ${data.deleted_rows} rows.`, false, true);
            showAdminActionPanel();
            hideAdminAuthPanel();
        })
        .catch((err) => {
            setAdminStatus(err.message || 'Database reset failed.', true);
        })
        .finally(() => {
            adminExitInProgress = false;
            adminAuthConfirmBtn.disabled = false;
            adminPassword.value = '';
        });
        return;
    }
=======
    adminExitInProgress = true;
    adminExitBtn.disabled = true;
    setAdminStatus('Authorizing exit...');
>>>>>>> a2e288f106bbc0ca2d7d6dbd75fdf89172035065

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
<<<<<<< HEAD
        adminAuthConfirmBtn.disabled = false;
        adminPassword.value = '';
    });
=======
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
>>>>>>> a2e288f106bbc0ca2d7d6dbd75fdf89172035065
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

<<<<<<< HEAD
function showSaveReadingUI() {
    saveReadingBtn.style.display = 'inline-flex';
}

function hideSaveReadingUI() {
    saveReadingBtn.style.display = 'none';
    closeSaveReadingModal();
}

function setSaveStatus(message, isError = false, isSuccess = false) {
    if (!message) return;
    phaseText.innerText = message;
    if (isError) {
        phaseText.style.color = '#b91c1c';
        return;
    }
    if (isSuccess) {
        phaseText.style.color = '#047857';
        return;
    }
    phaseText.style.color = '#0ea5e9';
}

function setSaveFormStatus(message, isError = false, isSuccess = false) {
    saveFormStatus.innerText = message;
    saveFormStatus.className = 'admin-status';
    if (isError) saveFormStatus.classList.add('error');
    if (isSuccess) saveFormStatus.classList.add('success');
}

function formatDateTimeForInput(date) {
    const pad = (v) => String(v).padStart(2, '0');
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

function openSaveReadingModal() {
    patientNameInput.value = '';
    measurementTimeInput.value = formatDateTimeForInput(new Date());
    predictedHbInput.value = latestReading.hb != null ? Number(latestReading.hb).toFixed(1) : '';
    predictedStatusInput.value = latestReading.status || '';
    setSaveFormStatus('Fill the form, then tap Submit.');
    saveReadingModal.style.display = 'flex';
    setTimeout(() => patientNameInput.focus(), 50);
}

function closeSaveReadingModal() {
    saveReadingModal.style.display = 'none';
    hideVirtualKeyboard();
    activeKeyboardInput = null;
}

saveReadingBtn.addEventListener('click', function() {
    if (latestReading.hb == null) {
        setSaveStatus('No completed reading available to save.', true);
        return;
    }
    openSaveReadingModal();
});

cancelSaveReadingBtn.addEventListener('click', function() {
    closeSaveReadingModal();
});

saveReadingModal.addEventListener('click', function(e) {
    if (e.target === saveReadingModal) closeSaveReadingModal();
});

saveReadingForm.addEventListener('submit', function(e) {
    e.preventDefault();
    if (saveReadingInProgress) return;

    const formData = new FormData(saveReadingForm);
    saveReadingInProgress = true;
    submitSaveReadingBtn.disabled = true;
    setSaveFormStatus('Saving reading...', false, false);

    fetch('/save_measurement', {
        method: 'POST',
        body: formData,
    })
    .then(async (res) => {
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
            throw new Error(data.error || 'Failed to save reading.');
        }

        setSaveFormStatus('Reading saved securely.', false, true);
        setSaveStatus(`Saved reading #${data.reading_id}.`, false, true);
        saveReadingBtn.style.display = 'none';
        setTimeout(closeSaveReadingModal, 500);
    })
    .catch((err) => {
        setSaveFormStatus(err.message || 'Failed to save reading.', true, false);
    })
    .finally(() => {
        saveReadingInProgress = false;
        submitSaveReadingBtn.disabled = false;
    });
});

function initializeCustomInputMode() {
    const vkInputs = document.querySelectorAll('.vk-input');
    vkInputs.forEach((inputEl) => {
        inputEl.setAttribute('inputmode', 'none');
        inputEl.setAttribute('autocorrect', 'off');
        inputEl.setAttribute('spellcheck', 'false');
    });

    document.addEventListener('focusin', function(e) {
        const target = e.target;
        if (target && target.classList && target.classList.contains('vk-input')) {
            activeKeyboardInput = target;
            showVirtualKeyboard();
        }
    });

    document.addEventListener('pointerdown', function(e) {
        if (!keyboardDock || keyboardDock.style.display !== 'block') return;
        if (keyboardDock.contains(e.target)) return;

        const targetIsInput = e.target && e.target.classList && e.target.classList.contains('vk-input');
        if (targetIsInput) return;

        hideVirtualKeyboard();
        activeKeyboardInput = null;
    });
}

function initializeVirtualKeyboard() {
    if (!virtualKeyboard) return;

    virtualKeyboard.innerHTML = '';
    KEYBOARD_ROWS.forEach((row) => {
        const rowDiv = document.createElement('div');
        rowDiv.className = 'vk-row';

        row.forEach((keyLabel) => {
            const keyBtn = document.createElement('button');
            keyBtn.type = 'button';
            keyBtn.className = 'vk-key';
            keyBtn.dataset.key = keyLabel;
            keyBtn.innerText = getRenderedKeyLabel(keyLabel);

            if (keyLabel === 'Space') keyBtn.classList.add('wide');
            if (keyLabel === 'Backspace' || keyLabel === 'Hide' || keyLabel === 'Clear' || keyLabel === 'Shift' || keyLabel === 'Enter') keyBtn.classList.add('action');

            keyBtn.addEventListener('click', function() {
                onVirtualKeyPress(keyLabel);
            });

            rowDiv.appendChild(keyBtn);
        });

        virtualKeyboard.appendChild(rowDiv);
    });

    refreshKeyboardLabels();
}

function getRenderedKeyLabel(baseKey) {
    const isSingleLetter = /^[A-Z]$/.test(baseKey);
    if (!isSingleLetter) return baseKey;
    return keyboardShift ? baseKey : baseKey.toLowerCase();
}

function refreshKeyboardLabels() {
    const keyButtons = virtualKeyboard.querySelectorAll('.vk-key');
    keyButtons.forEach((button) => {
        const key = button.dataset.key || '';
        button.innerText = getRenderedKeyLabel(key);
        if (key === 'Shift') {
            button.classList.toggle('active-shift', keyboardShift);
        }
    });
}

function showVirtualKeyboard() {
    document.body.classList.add('keyboard-open');
    keyboardDock.style.display = 'block';
}

function hideVirtualKeyboard() {
    document.body.classList.remove('keyboard-open');
    keyboardDock.style.display = 'none';
}

function onVirtualKeyPress(keyLabel) {
    if (!activeKeyboardInput) return;

    if (keyLabel === 'Shift') {
        keyboardShift = !keyboardShift;
        refreshKeyboardLabels();
        return;
    }

    if (keyLabel === 'Hide') {
        hideVirtualKeyboard();
        return;
    }

    if (keyLabel === 'Enter') {
        if (activeKeyboardInput === adminPassword && adminAuthPanel.style.display !== 'none') {
            runPendingAdminAction();
            return;
        }
        if (saveReadingModal.style.display === 'flex') {
            submitSaveReadingBtn.click();
            return;
        }
    }

    if (keyLabel === 'Clear') {
        activeKeyboardInput.value = '';
        activeKeyboardInput.dispatchEvent(new Event('input', { bubbles: true }));
        return;
    }

    const isBackspace = keyLabel === 'Backspace';
    const insertText = keyLabel === 'Space' ? ' ' : getRenderedKeyLabel(keyLabel);
    const start = activeKeyboardInput.selectionStart ?? activeKeyboardInput.value.length;
    const end = activeKeyboardInput.selectionEnd ?? activeKeyboardInput.value.length;
    const currentValue = activeKeyboardInput.value || '';

    let newValue = currentValue;
    let nextCursor = start;

    if (isBackspace) {
        if (start === end && start > 0) {
            newValue = currentValue.slice(0, start - 1) + currentValue.slice(end);
            nextCursor = start - 1;
        } else {
            newValue = currentValue.slice(0, start) + currentValue.slice(end);
            nextCursor = start;
        }
    } else {
        newValue = currentValue.slice(0, start) + insertText + currentValue.slice(end);
        nextCursor = start + insertText.length;
    }

    activeKeyboardInput.value = newValue;
    activeKeyboardInput.focus();
    activeKeyboardInput.setSelectionRange(nextCursor, nextCursor);
    activeKeyboardInput.dispatchEvent(new Event('input', { bubbles: true }));

    if (keyboardShift && /^[A-Za-z]$/.test(insertText)) {
        keyboardShift = false;
        refreshKeyboardLabels();
    }
}

=======
>>>>>>> a2e288f106bbc0ca2d7d6dbd75fdf89172035065
function updateUI(hb, status) {
    document.getElementById('hb_val').innerText = hb;
    const badge = document.getElementById('status_badge');
    badge.style.opacity = '1';
    badge.innerText = status;
    
<<<<<<< HEAD
    badge.className = "status-badge large-badge"; // reset
=======
    badge.className = "status-badge"; // reset
>>>>>>> a2e288f106bbc0ca2d7d6dbd75fdf89172035065
    if(status.includes("Severe")) badge.classList.add("severe");
    else if(status.includes("Mild") || status.includes("Moderate")) badge.classList.add("mild");
    else badge.classList.add("normal");
}