/* Scan dialog for the medicine box: camera / hardware scanner / manual PZN -> lookup -> confirm. */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const dialog = $("tile-dialog");
  if (!dialog) return;

  const lookupUrl = dialog.dataset.lookupUrl;
  const input = $("pzn-input");
  const err = $("scan-err");
  const camMsg = $("cam-msg");
  const video = $("cam");
  const finder = $("viewfinder");
  const camToggle = $("cam-toggle");
  const scanSection = $("scan-section");
  const openScan = $("open-scan");
  const openCamera = $("open-camera");
  const scanActions = $("scan-actions");
  let stream = null;
  let timer = null;
  let lastRaw = "";
  let qty = 1;

  /* ---- tile open / close ---- */
  if (dialog.dataset.open === "1" && typeof dialog.showModal === "function") dialog.showModal();
  $("tile-close").addEventListener("click", () => dialog.close());

  // The scan section only exists for caregivers; doctors only get the tile and comments.
  if (!openScan) return;
  dialog.addEventListener("close", hideScan);
  function showScan() {
    reset();
    scanSection.hidden = false;
    scanActions.hidden = true;
  }
  openScan.addEventListener("click", () => { showScan(); input.focus(); });
  $("scan-close").addEventListener("click", hideScan);

  function hideScan() {
    stopCamera();
    if (scanSection) scanSection.hidden = true;
    if (scanActions) scanActions.hidden = false;
  }

  function reset() {
    err.textContent = "";
    camMsg.textContent = "";
    input.value = "";
    lastRaw = "";
    $("confirm").hidden = true;
    setQty(1);
  }

  /* ---- lookup ---- */
  async function lookup(code) {
    err.textContent = "";
    let data;
    try {
      const res = await fetch(lookupUrl + "?code=" + encodeURIComponent(code), { credentials: "same-origin" });
      data = await res.json();
    } catch (e) {
      err.textContent = "Lookup failed. Check your connection and try again.";
      return false;
    }
    if (!data.ok) { err.textContent = data.error; return false; }
    showConfirm(data.pzn, data.medicine);
    return true;
  }

  function makePill(m) {
    const p = document.createElement("span");
    p.className = "pill " + m.shape + (m.scored ? " scored" : "");
    p.style.setProperty("--c", m.color);
    if (m.color2) p.style.setProperty("--c2", m.color2);
    return p;
  }

  function showConfirm(pzn, med) {
    $("pzn-final").value = pzn;
    $("c-pzn").textContent = "PZN " + pzn;
    const thumb = $("c-thumb");
    thumb.textContent = "";
    if (med) {
      $("c-name").textContent = med.name + " " + med.strength;
      $("c-desc").textContent = med.form + ", " + med.shape + (med.imprint ? ', imprint "' + med.imprint + '"' : "");
      if (med.image_url) {
        const img = document.createElement("img");
        img.src = med.image_url; img.alt = "";
        thumb.appendChild(img);
      } else {
        thumb.appendChild(makePill(med));
      }
    } else {
      $("c-name").textContent = "Unknown medicine";
      $("c-desc").textContent = "Not in the local database yet. It will still be saved by PZN.";
      thumb.textContent = "?";
    }
    $("confirm").hidden = false;
    $("save-btn").focus();
  }

  $("pzn-look").addEventListener("click", () => lookup(input.value));
  input.addEventListener("keydown", (e) => {
    // Hardware scanners end with Enter: look up instead of submitting.
    if (e.key === "Enter") { e.preventDefault(); lookup(input.value); }
  });
  document.querySelectorAll(".chipbtn").forEach((b) =>
    b.addEventListener("click", () => { input.value = b.dataset.pzn; lookup(b.dataset.pzn); }));

  /* ---- quantity ---- */
  function setQty(n) {
    qty = Math.max(1, Math.min(4, n));
    $("qty-val").textContent = qty;
    $("qty-final").value = qty;
  }
  $("qty-minus").addEventListener("click", () => setQty(qty - 1));
  $("qty-plus").addEventListener("click", () => setQty(qty + 1));

  /* ---- camera scanning (back camera on phones) ---- */
  camToggle.addEventListener("click", () => (stream ? stopCamera() : startCamera()));

  // "Scan pack with camera" on the tile: open the scan section and start the camera right away.
  openCamera.addEventListener("click", () => { showScan(); startCamera(); });

  const FORMATS = ["qr_code", "data_matrix", "code_39", "code_128", "ean_13"];

  function loadScript(url) {
    return new Promise((resolve, reject) => {
      const el = document.createElement("script");
      el.src = url;
      el.onload = resolve;
      el.onerror = () => reject(new Error("script failed: " + url));
      document.head.appendChild(el);
    });
  }

  /* Returns async detect(video) -> raw string | null.
     Uses the browser's BarcodeDetector when it can read our formats (Chrome on Android/Mac),
     otherwise the bundled ZXing decoder (Firefox, Safari, Chrome on Linux/Windows). */
  async function createDecoder() {
    if ("BarcodeDetector" in window) {
      try {
        const supported = await BarcodeDetector.getSupportedFormats();
        const formats = FORMATS.filter((f) => supported.includes(f));
        if (formats.includes("qr_code") && formats.includes("data_matrix") && formats.includes("code_39")) {
          const det = new BarcodeDetector({ formats });
          return async (v) => {
            const codes = await det.detect(v);
            return codes.length ? codes[0].rawValue : null;
          };
        }
      } catch (e) { /* fall through to ZXing */ }
    }
    if (!window.ZXing) await loadScript(dialog.dataset.zxingUrl);
    const Z = window.ZXing;
    const hints = new Map();
    hints.set(Z.DecodeHintType.POSSIBLE_FORMATS, [
      Z.BarcodeFormat.QR_CODE, Z.BarcodeFormat.DATA_MATRIX, Z.BarcodeFormat.CODE_39,
      Z.BarcodeFormat.CODE_128, Z.BarcodeFormat.EAN_13,
    ]);
    hints.set(Z.DecodeHintType.TRY_HARDER, true);
    const reader = new Z.MultiFormatReader();
    reader.setHints(hints);
    const canvas = document.createElement("canvas");
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    return async (v) => {
      const scale = Math.min(1, 960 / v.videoWidth);
      canvas.width = Math.round(v.videoWidth * scale);
      canvas.height = Math.round(v.videoHeight * scale);
      ctx.drawImage(v, 0, 0, canvas.width, canvas.height);
      const rgba = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
      const lum = new Uint8ClampedArray(canvas.width * canvas.height);
      for (let i = 0, j = 0; i < rgba.length; i += 4, j++) {
        lum[j] = (rgba[i] * 306 + rgba[i + 1] * 601 + rgba[i + 2] * 117) >> 10;
      }
      const bitmap = new Z.BinaryBitmap(new Z.HybridBinarizer(new Z.RGBLuminanceSource(lum, canvas.width, canvas.height)));
      try {
        return reader.decode(bitmap).getText();
      } catch (e) {
        return null; // nothing readable in this frame
      }
    };
  }

  async function startCamera() {
    if (stream) return;
    camMsg.textContent = "";
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      camMsg.textContent = "Camera needs HTTPS (or localhost). Type the PZN or use a hardware scanner instead.";
      return;
    }
    camMsg.textContent = "Starting camera...";
    let decode;
    try {
      decode = await createDecoder();
    } catch (e) {
      camMsg.textContent = "Could not load the barcode reader. Type the PZN instead.";
      return;
    }
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } },
        audio: false,
      });
    } catch (e) {
      camMsg.textContent = e && e.name === "NotFoundError"
        ? "No camera found. Type the PZN or use a hardware scanner."
        : "Camera access was blocked. Allow it in the browser, or type the PZN.";
      return;
    }
    video.srcObject = stream;
    try { await video.play(); } catch (e) { /* autoplay quirks: stream still attached */ }
    finder.hidden = false;
    camToggle.textContent = "Stop camera";
    camMsg.textContent = "Hold the barcode or QR code on the pack steady in the frame.";
    let busy = false;
    timer = setInterval(async () => {
      if (busy || !stream || video.readyState < 2 || !video.videoWidth) return;
      busy = true;
      try {
        const raw = await decode(video);
        if (raw && raw !== lastRaw) {
          lastRaw = raw;
          input.value = raw;
          if (await lookup(raw)) stopCamera();
        }
      } catch (e) { /* frame not ready; try again */ }
      busy = false;
    }, 250);
  }

  function stopCamera() {
    clearInterval(timer);
    timer = null;
    if (stream) stream.getTracks().forEach((t) => t.stop());
    stream = null;
    video.srcObject = null;
    finder.hidden = true;
    camToggle.textContent = "📷 Start camera";
  }
})();
