// HANDLE UPLOAD
document.getElementById('fileInput').addEventListener('change', function(e) {
    const file = e.target.files[0];
    if (!file) return;

    const formData = new FormData();
    formData.append('file', file);

    // Show loading state
    document.getElementById('hb_val').innerText = "...";
    document.getElementById('status_badge').style.opacity = '1';
    document.getElementById('status_badge').innerText = "Processing...";
    document.getElementById('status_badge').className = "status-badge mild";

    fetch('/upload', { method: 'POST', body: formData })
    .then(response => response.json())
    .then(data => {
        updateUI(data.hb, data.status);
    });
});

// POLLING FOR CAMERA (Optional if using upload)
setInterval(function() {
    // Only poll if we aren't actively uploading
    fetch('/get_result')
    .then(res => res.json())
    .then(data => {
        // If the camera is running, it will send data > 0
        if(data.hb > 0) updateUI(data.hb, data.status);
    });
}, 1000);

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