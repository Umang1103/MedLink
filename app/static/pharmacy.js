(() => {
  let activeStop;
  document.querySelectorAll('.scan-barcode').forEach(button => {
    const tools = button.closest('.scanner-tools');
    const video = tools.querySelector('video');
    const status = tools.querySelector('.scanner-status');
    const stopButton = tools.querySelector('.stop-camera');
    button.addEventListener('click', async () => {
      activeStop?.();
      if (!('BarcodeDetector' in window) || !navigator.mediaDevices?.getUserMedia) {
        status.textContent = 'Camera barcode scanning is not available in this browser. Use a scanner or enter the printed PZN.';
        return;
      }
      let stream, running = true;
      const stop = () => {
        running = false;
        stream?.getTracks().forEach(track => track.stop());
        video.srcObject = null; video.hidden = true; stopButton.hidden = true; button.disabled = false;
      };
      activeStop = stop;
      stopButton.onclick = stop;
      try {
        button.disabled = true;
        const formats = await BarcodeDetector.getSupportedFormats();
        const detector = new BarcodeDetector({formats: ['code_39', 'code_128', 'ean_13', 'data_matrix'].filter(f => formats.includes(f))});
        stream = await navigator.mediaDevices.getUserMedia({video: {facingMode: 'environment'}, audio: false});
        if (!running) { stream.getTracks().forEach(track => track.stop()); return; }
        video.srcObject = stream; video.hidden = false; stopButton.hidden = false;
        await video.play();
        status.textContent = 'Point the camera at the package barcode. Confirm the PZN before submitting.';
        const tick = async () => {
          if (!running) return;
          try {
            const found = await detector.detect(video);
            if (!running) return;
            if (found.length) {
              document.getElementById(button.dataset.input).value = found[0].rawValue;
              status.textContent = 'Barcode captured. Verify the PZN; unsupported encodings require manual entry.';
              stop(); return;
            }
          } catch (_) { /* Allow the next video frame to become ready. */ }
          if (running) setTimeout(tick, 200);
        };
        tick();
      } catch (_) {
        stop(); status.textContent = 'Camera could not be opened. Use HTTPS/localhost, allow camera access, or enter PZN manually.';
      }
    });
  });
  document.querySelectorAll('.pill-camera').forEach(panel => {
    const open = panel.querySelector('.take-pill-photo');
    const capture = panel.querySelector('.capture-pill');
    const cancel = panel.querySelector('.cancel-pill-camera');
    const video = panel.querySelector('video');
    const preview = panel.querySelector('.pill-preview');
    const status = panel.querySelector('.pill-camera-status');
    const input = document.getElementById(open.dataset.input);
    let stream, generation = 0, previewUrl;
    const stop = () => {
      generation++;
      stream?.getTracks().forEach(track => track.stop());
      stream = null; video.srcObject = null;
      video.hidden = capture.hidden = cancel.hidden = true; open.disabled = false;
    };
    input.addEventListener('change', () => {
      if (!input.files[0]) return;
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      previewUrl = URL.createObjectURL(input.files[0]);
      preview.src = previewUrl; preview.hidden = false;
      status.textContent = 'Photo attached. Confirm the medicine to save it.';
    });
    open.addEventListener('click', async () => {
      activeStop?.();
      if (!navigator.mediaDevices?.getUserMedia || !window.DataTransfer) {
        input.click(); return;
      }
      activeStop = stop;
      const version = ++generation;
      open.disabled = true;
      try {
        const requested = await navigator.mediaDevices.getUserMedia({video: {facingMode: 'environment'}, audio: false});
        if (version !== generation) { requested.getTracks().forEach(track => track.stop()); return; }
        stream = requested; video.srcObject = stream; video.hidden = false; cancel.hidden = false;
        await video.play();
        if (version !== generation) return;
        capture.hidden = false; status.textContent = 'Position the pill and press Capture photo.';
      } catch (_) {
        stop(); status.textContent = 'Camera unavailable. Select a photo or use your device camera.';
        input.click();
      }
    });
    capture.addEventListener('click', () => {
      if (!video.videoWidth || !video.videoHeight) return;
      const canvas = document.createElement('canvas');
      const scale = Math.min(1, 1600 / Math.max(video.videoWidth, video.videoHeight));
      canvas.width = Math.round(video.videoWidth * scale); canvas.height = Math.round(video.videoHeight * scale);
      canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
      capture.disabled = true;
      canvas.toBlob(blob => {
        capture.disabled = false;
        if (!blob) { status.textContent = 'Capture failed. Please try again.'; return; }
        const files = new DataTransfer();
        files.items.add(new File([blob], 'pill-photo.jpg', {type: 'image/jpeg'}));
        input.files = files.files;
        input.dispatchEvent(new Event('change', {bubbles: true}));
        stop();
      }, 'image/jpeg', 0.9);
    });
    cancel.addEventListener('click', stop);
    window.addEventListener('pagehide', () => { stop(); if (previewUrl) URL.revokeObjectURL(previewUrl); });
  });
  window.addEventListener('pagehide' , () => activeStop?.());
})();
