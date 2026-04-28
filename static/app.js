const $ = (id) => document.getElementById(id);

let screens = {};

const state = {
  me: null,
  receiver: null,
  authCheckVersion: 0,
  camera: { stream: null, raf: null, active: false, scanStartTime: null },
  activity: { timer: null },
  run: { active: false, watchId: null, points: [], lastPos: null, totalKm: 0, currentSpeed: 0 },
  buy: { selectedCoins: 0, selectedPrice: 0 },
};

// ── SCREEN MANAGEMENT ─────────────────────────────────────────────────────

function showScreen(name) {
  Object.entries(screens).forEach(([key, el]) => {
    if (!el) return;
    if (key === name) el.classList.remove('hidden');
    else el.classList.add('hidden');
  });
}

function showSplash(visible) {
  const splash = $('splash');
  if (!splash) return;
  if (visible) splash.classList.remove('hidden');
  else splash.classList.add('hidden');
}

function normalizeCode(s) { return (s || '').toString().trim().toUpperCase(); }

function setMsg(el, msg, kind = 'error') {
  if (!el) return;
  el.textContent = msg || '';
  el.style.color = kind === 'ok' ? 'var(--ok)' : 'var(--danger)';
}

async function api(path, method = 'GET', body) {
  const opts = { method, headers: {}, credentials: 'include' };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = data && data.error ? data.error : `Request failed (${res.status})`;
    throw new Error(err);
  }
  return data;
}

// ── SPLASH LOGO CLICK ─────────────────────────────────────────────────────

function initSplash() {
  const btn = $('splashLogoBtn');
  const loader = $('splashLoader');
  const hint = $('splashHint');
  if (!btn) return;

  // After initial check for session, show tap hint if not logged in
  window._splashTapReady = false;

  btn.addEventListener('click', () => {
    if (!window._splashTapReady) return; // not ready yet (still checking session)
    // Animate then reveal auth
    btn.style.transform = 'scale(0.9)';
    setTimeout(() => { btn.style.transform = ''; }, 150);
    setTimeout(() => {
      showSplash(false);
      showScreen('auth');
    }, 220);
  });

  btn.style.transition = 'transform 0.18s ease';
}

// ── QR ────────────────────────────────────────────────────────────────────

function renderQr(boxId, qrText) {
  const box = $(boxId);
  if (!box) return;
  box.innerHTML = '';
  new QRCode(box, {
    text: qrText,
    width: 200,
    height: 200,
    colorDark: '#ffffff',
    colorLight: '#000000',
    correctLevel: QRCode.CorrectLevel.M,
  });
}

// ── DASHBOARD ─────────────────────────────────────────────────────────────

async function refreshDashboard() {
  state.me = await api('/api/me');
  const coinValue = state.me.coinvalue;
  const rupeeValue = (coinValue * 10000).toLocaleString('en-IN');
  $('meCodeName').textContent = state.me.code_name;
  $('meCoinValue').textContent = `${coinValue} (₹${rupeeValue})`;
  $('meCoin').textContent = String(state.me.coin);
  $('sidebarMeCodeName').textContent = state.me.code_name;
  $('sidebarMeCoinValue').textContent = `${coinValue} (₹${rupeeValue})`;
  $('sidebarMeCoin').textContent = state.me.coin;
  const qr = await api('/api/qr/mine');
  renderQr('myQrBox', qr.qrText);
  await loadTransactions();
}

async function loadTransactions() {
  const txRes = await api('/api/transactions');
  const list = $('txList');
  if (!list) return;
  list.innerHTML = '';
  const txs = txRes.transactions || [];
  if (!txs.length) {
    list.innerHTML = '<div class="tx-item muted">No transactions yet.</div>';
    return;
  }
  const myCode = state.me ? state.me.code_name : '';
  txs.slice(0, 4).forEach((t) => {
    const who =
      t.payer_code_name === myCode ? 'You paid' :
      t.receiver_code_name === myCode ? 'You received' : 'Transaction';
    const rupeeAmount = (t.amount * 10000).toLocaleString('en-IN');
    const div = document.createElement('div');
    div.className = 'tx-item';
    div.innerHTML = `
      <div class="tx-title">${who}</div>
      <div class="tx-sub">Amount: <span style="color:var(--gold2);font-weight:900">${t.amount} coin${t.amount !== 1 ? 's' : ''}</span> <span class="muted small">(₹${rupeeAmount})</span></div>
      <div class="tx-sub">Time: ${t.created_at}</div>
    `;
    list.appendChild(div);
  });
}

async function checkLoginOnLoad() {
  const v = ++state.authCheckVersion;
  try {
    const me = await api('/api/me');
    if (v !== state.authCheckVersion) return;
    showSplash(false);
    $('loginStatus').textContent = 'Login verified';
    state.me = me;
    showScreen('dashboard');
    await refreshDashboard();
  } catch {
    if (v !== state.authCheckVersion) return;
    // Not logged in - show splash with tap-to-continue
    const loader = $('splashLoader');
    const hint = $('splashHint');
    if (loader) loader.style.display = 'none';
    if (hint) hint.style.display = 'block';
    window._splashTapReady = true;
  }
}

function enforceUppercaseInputs() {
  ['regUserId','regCodeName','loginCodeName','searchCodeName','manualReceiverCodeName'].forEach((id) => {
    const el = $(id);
    if (!el) return;
    el.addEventListener('input', () => { el.value = normalizeCode(el.value); });
  });
}

// ── BUY COINS ────────────────────────────────────────────────────────────

function initBuyCoins() {
  const pkgs = document.querySelectorAll('.coin-pkg');
  pkgs.forEach((pkg) => {
    pkg.addEventListener('click', () => {
      pkgs.forEach(p => p.classList.remove('selected'));
      pkg.classList.add('selected');
      const coins = parseInt(pkg.dataset.coins);
      const price = parseInt(pkg.dataset.price); // in paise
      state.buy = { selectedCoins: coins, selectedPrice: price };
      // Show selected info
      const info = $('selectedPkgInfo');
      const txt = $('selectedPkgText');
      const priceRs = (price / 100).toLocaleString('en-IN');
      if (info && txt) {
        info.classList.remove('hidden');
        txt.textContent = `${coins} Coin${coins > 1 ? 's' : ''} for \u20B9${priceRs}`;
      }
      const btn = $('btnBuyCoins');
      if (btn) {
        btn.disabled = false;
        btn.textContent = `Buy ${coins} Coin${coins > 1 ? 's' : ''} for \u20B9${priceRs}`;
      }
    });
  });

  const clearBtn = $('btnClearPkg');
  if (clearBtn) {
    clearBtn.addEventListener('click', () => {
      pkgs.forEach(p => p.classList.remove('selected'));
      state.buy = { selectedCoins: 0, selectedPrice: 0 };
      const info = $('selectedPkgInfo');
      if (info) info.classList.add('hidden');
      const btn = $('btnBuyCoins');
      if (btn) { btn.disabled = true; btn.textContent = 'Select a Package to Buy'; }
    });
  }

  const buyBtn = $('btnBuyCoins');
  if (buyBtn) {
    buyBtn.addEventListener('click', () => {
      if (!state.buy.selectedCoins) return;
      if (!state.me) { setMsg($('buyMsg'), 'Please login first.'); return; }
      launchRazorpay(state.buy.selectedCoins, state.buy.selectedPrice);
    });
  }
}

function launchRazorpay(coins, amountPaise) {
  const buyMsg = $('buyMsg');
  setMsg(buyMsg, 'Opening payment gateway...', 'ok');

  // Create Razorpay order via backend
  api('/api/payment/create-order', 'POST', { coins, amount: amountPaise })
    .then((order) => {
      const options = {
        key: 'rzp_test_SiOpSGdjiLhfLU',
        amount: order.amount,
        currency: 'INR',
        name: 'Nova Coins',
        description: `${coins} Nova Coin${coins > 1 ? 's' : ''}`,
        order_id: order.razorpay_order_id,
        handler: function(response) {
          // Payment captured - verify and credit coins
          verifyAndCreditCoins(response, coins, amountPaise);
        },
        prefill: {
          name: state.me ? state.me.code_name : '',
          contact: '',
        },
        notes: {
          code_name: state.me ? state.me.code_name : '',
          coins: String(coins),
        },
        theme: { color: '#d4af37' },
        modal: {
          ondismiss: function() {
            setMsg(buyMsg, 'Payment cancelled.');
          }
        }
      };
      const rzp = new Razorpay(options);
      rzp.on('payment.failed', function(resp) {
        setMsg(buyMsg, 'Payment failed: ' + (resp.error.description || 'Unknown error'));
      });
      setMsg(buyMsg, '');
      rzp.open();
    })
    .catch((e) => {
      setMsg(buyMsg, 'Could not initiate payment: ' + (e.message || e));
    });
}

async function verifyAndCreditCoins(rzpResponse, coins, amountPaise) {
  const buyMsg = $('buyMsg');
  setMsg(buyMsg, 'Verifying payment & crediting coins...', 'ok');
  try {
    const res = await api('/api/payment/verify', 'POST', {
      razorpay_order_id:   rzpResponse.razorpay_order_id,
      razorpay_payment_id: rzpResponse.razorpay_payment_id,
      razorpay_signature:  rzpResponse.razorpay_signature,
      coins:  coins,
      amount: amountPaise,
    });
    if (res.ok) {
      const rupeeAdded = (coins * 10000).toLocaleString('en-IN');
      setMsg(buyMsg, `${coins} coins (Rs ${rupeeAdded}) credited! New balance: ${res.new_balance}`, 'ok');
      await refreshDashboard();
      // Show invoice
      showInvoice({
        invoice_no:   res.invoice_no,
        payment_id:   rzpResponse.razorpay_payment_id,
        order_id:     rzpResponse.razorpay_order_id,
        code_name:    state.me.code_name,
        coins:        coins,
        amount_paise: amountPaise,
        new_balance:  res.new_balance,
        paid_at:      new Date().toLocaleString('en-IN'),
      });
    } else {
      setMsg(buyMsg, res.error || 'Verification failed.');
    }
  } catch(e) {
    setMsg(buyMsg, 'Credit failed: ' + (e.message || e));
  }
}

// ── INVOICE ───────────────────────────────────────────────────────────────

function showInvoice(data) {
  const modal = $('invoiceModal');
  const body = $('invoiceContent');
  if (!modal || !body) return;

  const amountRs = (data.amount_paise / 100).toLocaleString('en-IN', {minimumFractionDigits: 2, maximumFractionDigits: 2});
  const pricePerCoin = ((data.amount_paise / 100) / data.coins).toLocaleString('en-IN', {minimumFractionDigits: 2, maximumFractionDigits: 2});
  const coinRupeeValue = (data.coins * 10000).toLocaleString('en-IN');

  body.innerHTML = `
    <div class="invoice-body">
      <div class="invoice-header">
        <div class="inv-brand">&#x25C6; NOVA</div>
        <div class="inv-sub">COIN PURCHASE RECEIPT</div>
      </div>
      <div class="inv-section">
        <div class="inv-row">
          <span class="inv-label">Invoice No.</span>
          <span class="inv-value">${data.invoice_no || 'INV-' + Date.now()}</span>
        </div>
        <div class="inv-row">
          <span class="inv-label">Date &amp; Time</span>
          <span class="inv-value">${data.paid_at}</span>
        </div>
        <div class="inv-row">
          <span class="inv-label">Account</span>
          <span class="inv-value">${data.code_name}</span>
        </div>
      </div>
      <div class="inv-section">
        <div class="inv-row">
          <span class="inv-label">Order ID</span>
          <span class="inv-value">${data.order_id || '-'}</span>
        </div>
        <div class="inv-row">
          <span class="inv-label">Payment ID</span>
          <span class="inv-value">${data.payment_id || '-'}</span>
        </div>
        <div class="inv-row">
          <span class="inv-label">Gateway</span>
          <span class="inv-value">Razorpay</span>
        </div>
      </div>
      <div class="inv-section">
        <div class="inv-row">
          <span class="inv-label">Item</span>
          <span class="inv-value">Nova Coins</span>
        </div>
        <div class="inv-row">
          <span class="inv-label">Quantity</span>
          <span class="inv-value">${data.coins} coin${data.coins > 1 ? 's' : ''}</span>
        </div>
        <div class="inv-row">
          <span class="inv-label">Rate (Purchase)</span>
          <span class="inv-value">&#8377;${pricePerCoin} / coin</span>
        </div>
        <div class="inv-row">
          <span class="inv-label">Coin Face Value</span>
          <span class="inv-value">&#8377;10,000 / coin</span>
        </div>
        <div class="inv-row">
          <span class="inv-label">Amount</span>
          <span class="inv-value">&#8377;${amountRs}</span>
        </div>
        <div class="inv-row">
          <span class="inv-label">Taxes &amp; Fees</span>
          <span class="inv-value">Included</span>
        </div>
      </div>
      <div class="inv-total">
        <span class="inv-total-label">TOTAL PAID</span>
        <span class="inv-total-value">&#8377;${amountRs}</span>
      </div>
      <div class="inv-status">PAYMENT CONFIRMED &mdash; COINS CREDITED</div>
      <div class="inv-section" style="margin-top:12px;">
        <div class="inv-row">
          <span class="inv-label">Coins Added</span>
          <span class="inv-value" style="color:var(--ok)">+${data.coins} (₹${coinRupeeValue})</span>
        </div>
        <div class="inv-row">
          <span class="inv-label">New Balance</span>
          <span class="inv-value" style="color:var(--gold2)">${data.new_balance} coins (₹${(data.new_balance * 10000).toLocaleString('en-IN')})</span>
        </div>
      </div>
      <div class="inv-footer">
        Thank you for using Nova!<br>
        For support, contact nova@support.in<br>
        <span style="font-size:10px;opacity:.6;">This is a computer-generated invoice. No signature required.</span>
      </div>
    </div>
  `;

  modal.classList.remove('hidden');
  modal.setAttribute('aria-hidden', 'false');
}

function closeInvoiceModal() {
  const modal = $('invoiceModal');
  if (modal) { modal.classList.add('hidden'); modal.setAttribute('aria-hidden', 'true'); }
}

// ── SCANNER ───────────────────────────────────────────────────────────────

function openScannerModal() {
  const modal = $('scannerModal');
  modal.classList.remove('hidden');
  modal.setAttribute('aria-hidden', 'false');
  $('scannerMsg').textContent = '';
  const pasteEl = $('qrPaste');
  if (pasteEl) { pasteEl.value = ''; pasteEl.style.borderColor = ''; }
  state.camera.scanStartTime = null;
  stopCamera();
  startCamera();
}

function closeScannerModal() {
  const modal = $('scannerModal');
  modal.classList.add('hidden');
  modal.setAttribute('aria-hidden', 'true');
  stopCamera();
}

function openAboutModal() {
  const modal = $('aboutModal');
  modal.classList.remove('hidden');
  modal.setAttribute('aria-hidden', 'false');
  startActivityPolling();
}

function closeAboutModal() {
  const modal = $('aboutModal');
  modal.classList.add('hidden');
  modal.setAttribute('aria-hidden', 'true');
  stopActivityPolling();
}

function openHistoryModal() {
  const modal = $('historyModal');
  if (!modal) return;
  modal.classList.remove('hidden');
  modal.setAttribute('aria-hidden', 'false');
  loadTransactions();
}

function closeHistoryModal() {
  const modal = $('historyModal');
  if (!modal) return;
  modal.classList.add('hidden');
  modal.setAttribute('aria-hidden', 'true');
}

function stopActivityPolling() {
  if (state.activity.timer) clearInterval(state.activity.timer);
  state.activity.timer = null;
}

async function renderActivityOnce() {
  const list = $('activityList');
  if (!list) return;
  try {
    const txRes = await api('/api/activity');
    const txs = txRes.activity || [];
    list.innerHTML = '';
    if (!txs.length) {
      list.innerHTML = '<div class="activity-item muted">No activity yet.</div>';
      return;
    }
    txs.slice(0, 4).forEach((t) => {
      const div = document.createElement('div');
      div.className = 'activity-item';
      div.innerHTML = `
        <div class="activity-line">${t.payer_code_name} &rarr; ${t.receiver_code_name}</div>
        <div class="activity-time">${t.created_at}</div>
      `;
      list.appendChild(div);
    });
  } catch (e) {
    list.innerHTML = `<div class="activity-item" style="color:var(--danger)">${e.message || e}</div>`;
  }
}

function startActivityPolling() {
  stopActivityPolling();
  renderActivityOnce();
  state.activity.timer = setInterval(renderActivityOnce, 3000);
}

// ── CAMERA ────────────────────────────────────────────────────────────────

function stopCamera() {
  const cam = state.camera;
  if (cam.raf) cancelAnimationFrame(cam.raf);
  cam.raf = null;
  cam.active = false;
  if (cam.stream) { cam.stream.getTracks().forEach((t) => t.stop()); cam.stream = null; }
}

async function startCamera() {
  const video = $('scannerVideo');
  const canvas = $('scannerCanvas');
  try {
    const isSecure = window.isSecureContext || location.protocol === 'https:' || location.hostname === 'localhost' || location.hostname === '127.0.0.1';
    if (!isSecure) { $('scannerMsg').textContent = 'Camera requires HTTPS. Use "Paste QR text" below.'; return; }
    const constraints = { video: { facingMode: { ideal: 'environment' } }, audio: false };
    let stream = null;
    if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
      stream = await navigator.mediaDevices.getUserMedia(constraints);
    } else {
      const legacy = navigator.getUserMedia || navigator.webkitGetUserMedia || navigator.mozGetUserMedia;
      if (!legacy) { $('scannerMsg').textContent = 'Camera not supported. Paste QR text below.'; return; }
      stream = await new Promise((resolve, reject) => { legacy.call(navigator, constraints, resolve, reject); });
    }
    state.camera.stream = stream;
    state.camera.active = true;
    video.muted = true;
    video.srcObject = stream;
    await new Promise((resolve) => { if (video.readyState >= 2) { resolve(); return; } video.onloadedmetadata = () => resolve(); });
    await video.play().catch(() => {});
    await new Promise((r) => setTimeout(r, 300));
    canvas.width = video.videoWidth || video.offsetWidth || 640;
    canvas.height = video.videoHeight || video.offsetHeight || 480;
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    $('scannerCanvas').classList.add('hidden');
    if (!state.camera.scanStartTime) state.camera.scanStartTime = Date.now();

    const scan = () => {
      if (!state.camera.active) return;
      if (Date.now() - state.camera.scanStartTime > 60000) {
        stopCamera();
        $('scannerMsg').textContent = 'QR scan timed out. Paste QR text below.';
        return;
      }
      if (typeof jsQR !== 'function') { stopCamera(); $('scannerMsg').textContent = 'QR decoder not loaded. Refresh page.'; return; }
      if (video.readyState < 2 || video.paused) { state.camera.raf = requestAnimationFrame(scan); return; }
      if (canvas.width !== video.videoWidth && video.videoWidth > 0) { canvas.width = video.videoWidth; canvas.height = video.videoHeight; }
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
      const img = ctx.getImageData(0, 0, canvas.width, canvas.height);
      const code = jsQR(img.data, img.width, img.height, { inversionAttempts: 'attemptBoth' });
      if (code && code.data) {
        const qrText = String(code.data).trim();
        state.camera.active = false;
        if (state.camera.raf) cancelAnimationFrame(state.camera.raf);
        state.camera.raf = null;
        stopCamera();
        const pasteEl = $('qrPaste');
        pasteEl.value = qrText;
        pasteEl.style.borderColor = 'var(--ok)';
        $('scannerMsg').textContent = 'QR detected! Press "Verify QR" to proceed.';
        $('scannerMsg').style.color = 'var(--ok)';
        return;
      }
      state.camera.raf = requestAnimationFrame(scan);
    };
    state.camera.raf = requestAnimationFrame(scan);
  } catch (e) {
    const msg = (e && e.name) ? `${e.name}: ${e.message || ''}` : (e?.message || String(e));
    $('scannerMsg').textContent = `Camera error: ${msg}. Allow permission or paste QR text.`;
  }
}

async function verifyQrTextAndGoPay(qrText) {
  try {
    $('scannerMsg').textContent = 'Verifying QR...';
    $('scannerMsg').style.color = 'var(--muted)';
    const verify = await api('/api/qr/verify', 'POST', { qrText });
    closeScannerModal();
    state.receiver = {
      receiver_code_name: normalizeCode(verify.receiver_code_name || verify.code_name),
      receiver_coin: String(verify.receiver_coin || verify.coin || '').trim(),
      timestamp: verify.timestamp,
    };
    showReceiverBox();
    showPayScreenWithScan();
  } catch (e) {
    $('scannerMsg').textContent = `QR verify failed: ${e.message || e}`;
    $('scannerMsg').style.color = 'var(--danger)';
  }
}

function showReceiverBox() {
  const box = $('receiverBox');
  if (!state.receiver) { box.classList.add('hidden'); box.innerHTML = ''; return; }
  box.classList.remove('hidden');
  box.innerHTML = `
    <div class="receiver-line">Receiver: <strong>${state.receiver.receiver_code_name}</strong></div>
    <div class="receiver-line">Account no: ${state.receiver.receiver_coin}</div>
    ${state.receiver.timestamp ? `<div class="muted small" style="margin-top:8px;">QR time: ${new Date(state.receiver.timestamp * 1000).toLocaleString()}</div>` : ''}
  `;
}

function showPayScreenWithScan() {
  $('manualReceiverBox').classList.add('hidden');
  $('payAmount').value = '';
  $('payPassword').value = '';
  $('payMsg').textContent = '';
  showScreen('pay');
}

function showPayScreenWithManual() {
  state.receiver = null;
  $('receiverBox').classList.add('hidden');
  $('manualReceiverCoin').value = '';
  $('payAmount').value = '';
  $('payPassword').value = '';
  $('payMsg').textContent = '';
  $('manualReceiverBox').classList.remove('hidden');
  showScreen('pay');
}

async function fetchReceiverCoinFromCode() {
  const code = normalizeCode($('manualReceiverCodeName').value);
  if (!code) return;
  if (state.me && code === normalizeCode(state.me.code_name)) {
    setMsg($('payMsg'), 'You cannot pay yourself.');
    $('manualReceiverCoin').value = '';
    state.receiver = null;
    return;
  }
  const res = await api(`/api/account/search?code_name=${encodeURIComponent(code)}`);
  $('manualReceiverCoin').value = res.coin;
  state.receiver = { receiver_code_name: res.code_name, receiver_coin: res.coin, timestamp: null };
  showReceiverBox();
}

async function submitPay() {
  try {
    $('payMsg').textContent = '';
    $('btnPayNow').disabled = true;
    const amount = $('payAmount').value;
    const payerPassword = $('payPassword').value;
    if (!amount || Number(amount) <= 0) throw new Error('Enter valid amount');
    if (!payerPassword) throw new Error('Password is required');
    if (!state.receiver) throw new Error('Receiver info missing');
    if (state.me && normalizeCode(state.receiver.receiver_code_name) === normalizeCode(state.me.code_name)) {
      throw new Error('You cannot pay yourself.');
    }
    const body = {
      amount: Number(amount),
      payer_password: payerPassword,
      receiver_code_name: state.receiver.receiver_code_name,
      receiver_coin: state.receiver.receiver_coin,
    };
    const res = await api('/api/transaction/pay', 'POST', body);
    const payerRupees = (res.payer_balance * 10000).toLocaleString('en-IN');
    const receiverRupees = (res.receiver_balance * 10000).toLocaleString('en-IN');
    $('successText').textContent = `Your balance: ${res.payer_balance} coins (₹${payerRupees}) | Receiver: ${res.receiver_balance} coins (₹${receiverRupees})`;
    showScreen('paySuccess');
  } catch (e) {
    $('payMsg').textContent = e.message || String(e);
    $('payMsg').style.color = 'var(--danger)';
  } finally {
    $('btnPayNow').disabled = false;
  }
}

// ── SHOW TAB ──────────────────────────────────────────────────────────────

function showTab(tab) {
  const r = $('card-register');
  const l = $('card-login');
  if (r) r.style.display = tab === 'register' ? 'block' : 'none';
  if (l) l.style.display = tab === 'login' ? 'block' : 'none';
  document.querySelectorAll('.auth-tab').forEach((btn, i) => {
    btn.classList.toggle('active', (i === 0) === (tab === 'register'));
  });
}
window.showTab = showTab;

// ── OTP STATE ─────────────────────────────────────────────────────────────
const otpState = { emailVerified: false };

// ── GOOGLE AUTH CALLBACK (called by GSI library) ──────────────────────────
window.handleGoogleCredential = async function(response) {
  const msgEl = $('authLoginMsg');
  try {
    setMsg(msgEl, 'Signing in with Google...', 'muted');
    const res = await api('/api/google-login', 'POST', { credential: response.credential });
    if (res.needs_setup) {
      // New Google user — pre-fill register form and switch to register tab
      showTab('register');
      if ($('regName'))  $('regName').value  = res.name  || '';
      if ($('regEmail')) $('regEmail').value = res.email || '';
      // Mark email as verified since it came from Google
      otpState.emailVerified = true;
      _showEmailVerified();
      $('emailSendRow').style.display  = 'none';
      $('emailOtpRow').style.display   = 'none';
      setMsg($('authRegisterMsg'), 'Google account linked. Please complete your profile to finish registration.');
      return;
    }
    $('loginStatus').textContent = 'Google login successful';
    showScreen('dashboard');
    await refreshDashboard();
  } catch (e) {
    setMsg(msgEl, e.message || 'Google login failed');
  }
};

function _showEmailVerified() {
  otpState.emailVerified = true;
  const badge = $('emailVerifiedBadge');
  if (badge) { badge.style.display = 'inline-flex'; }
  const wrap = $('verifiedBadges');
  if (wrap) { wrap.style.display = 'flex'; }
  const row = $('emailOtpRow');
  if (row) row.style.display = 'none';
  const sendRow = $('emailSendRow');
  if (sendRow) sendRow.innerHTML = '<div class="verified-line">Email address verified</div>';
}

// ── BIND EVENTS ───────────────────────────────────────────────────────────

function bindEvents() {
  $('regCodeName').addEventListener('input', () => setMsg($('authRegisterMsg'), ''));
  $('loginCodeName').addEventListener('input', () => setMsg($('authLoginMsg'), ''));

  // ── SEND EMAIL OTP ──
  $('btnSendEmailOtp').addEventListener('click', async () => {
    const email = ($('regEmail').value || '').trim();
    if (!email || !email.includes('@')) { setMsg($('authRegisterMsg'), 'Enter a valid email first'); return; }
    const btn = $('btnSendEmailOtp');
    btn.disabled = true;
    btn.textContent = 'Sending...';
    try {
      await api('/api/otp/send-email', 'POST', { email });
      $('emailOtpRow').style.display = 'block';
      setMsg($('authRegisterMsg'), 'OTP sent to ' + email);
      btn.textContent = 'Resend Email OTP';
    } catch (e) {
      setMsg($('authRegisterMsg'), e.message || 'Failed to send email OTP');
      btn.textContent = 'Send Email OTP';
    } finally {
      btn.disabled = false;
    }
  });

  // ── VERIFY EMAIL OTP ──
  $('btnVerifyEmailOtp').addEventListener('click', async () => {
    const email = ($('regEmail').value || '').trim();
    const otp   = ($('regEmailOtp').value || '').trim();
    if (!otp) { $('emailOtpStatus').textContent = 'Enter the OTP'; return; }
    try {
      await api('/api/otp/verify-email', 'POST', { email, otp });
      _showEmailVerified();
    } catch (e) {
      $('emailOtpStatus').textContent = e.message || 'Invalid OTP';
    }
  });

  // ── SEND PHONE OTP ──
  // Phone OTP removed (Twilio costs money). Phone is stored as plain field.

  // ── REGISTER ──
  $('btnRegister').addEventListener('click', async () => {
    state.authCheckVersion++;
    try {
      $('btnRegister').disabled = true;
      $('authRegisterMsg').textContent = '';

      const name     = ($('regName').value || '').trim();
      const email    = ($('regEmail').value || '').trim();
      const phone    = ($('regPhone').value || '').trim();
      const password = ($('regPassword').value || '').trim();
      const confirm  = ($('regPasswordConfirm').value || '').trim();
      const payload  = {
        name,
        email,
        phone,
        user_id:   normalizeCode($('regUserId').value),
        code_name: normalizeCode($('regCodeName').value),
        password,
      };

      if (!name)                                          throw new Error('Full name is required');
      const codeLen = payload.code_name.length;
      if (codeLen < 5 || codeLen > 7)                    throw new Error('Code name must be 5-7 characters');
      if (!email || !email.includes('@'))                 throw new Error('Valid email is required');
      if (!otpState.emailVerified)                        throw new Error('Please verify your email first');
      if (!password || password.length < 12 || password.length > 16)
                                                          throw new Error('Password must be 12-16 characters');
      if (password !== confirm)                           throw new Error('Passwords do not match');

      const res = await api('/api/register', 'POST', payload);
      await api('/api/login', 'POST', { code_name: res.code_name, password });
      $('loginStatus').textContent = 'Account created';
      showScreen('dashboard');
      await refreshDashboard();
    } catch (e) {
      setMsg($('authRegisterMsg'), e.message || String(e));
      showScreen('auth');
    } finally {
      $('btnRegister').disabled = false;
    }
  });

  // ── LOGIN ──
  $('btnLogin').addEventListener('click', async () => {
    state.authCheckVersion++;
    try {
      $('btnLogin').disabled = true;
      $('authLoginMsg').textContent = '';
      const payload = {
        code_name: normalizeCode($('loginCodeName').value),
        password:  ($('loginPassword').value || '').trim(),
      };
      if (!payload.code_name) throw new Error('Code name required');
      if (!payload.password)  throw new Error('Password required');
      await api('/api/login', 'POST', payload);
      $('loginStatus').textContent = 'Login successful';
      showScreen('dashboard');
      await refreshDashboard();
    } catch (e) {
      setMsg($('authLoginMsg'), e.message || String(e));
      showScreen('auth');
    } finally {
      $('btnLogin').disabled = false;
    }
  });

  $('btnLogout').addEventListener('click', async () => {
    state.authCheckVersion++;
    try { await api('/api/logout', 'POST'); } catch {}
    state.me = null;
    state.receiver = null;
    showScreen('auth');
    $('loginStatus').textContent = '';
  });

  $('btnAbout').addEventListener('click', () => openAboutModal());
  $('btnCloseAbout').addEventListener('click', () => closeAboutModal());

  const btnCloseHistory = $('btnCloseHistory');
  if (btnCloseHistory) btnCloseHistory.addEventListener('click', () => closeHistoryModal());

  // Invoice buttons
  const btnCloseInvoice = $('btnCloseInvoice');
  if (btnCloseInvoice) btnCloseInvoice.addEventListener('click', closeInvoiceModal);
  const btnCloseInvoice2 = $('btnCloseInvoice2');
  if (btnCloseInvoice2) btnCloseInvoice2.addEventListener('click', closeInvoiceModal);
  const btnPrintInvoice = $('btnPrintInvoice');
  if (btnPrintInvoice) btnPrintInvoice.addEventListener('click', () => window.print());
  const invoiceModal = $('invoiceModal');
  if (invoiceModal) invoiceModal.addEventListener('click', (e) => { if (e.target === invoiceModal) closeInvoiceModal(); });

  $('btnSearch').addEventListener('click', async () => {
    const code = normalizeCode($('searchCodeName').value);
    const box = $('searchResult');
    box.classList.add('hidden');
    box.innerHTML = '';
    try {
      if (!code) throw new Error('Enter code name');
      const res = await api(`/api/account/search?code_name=${encodeURIComponent(code)}`);
      box.classList.remove('hidden');
      box.innerHTML = `
        <div class="muted">Account found</div>
        <div class="receiver-line" style="margin-top:8px;">User name: ${res.code_name}</div>
        <div class="receiver-line">Account no (coin): ${res.coin}</div>
        <div class="receiver-line">Balance: ${res.coinvalue} Coin${res.coinvalue !== 1 ? 's' : ''} <span class="muted small">(₹${(res.coinvalue * 10000).toLocaleString('en-IN')})</span></div>
      `;
    } catch (e) {
      box.classList.remove('hidden');
      box.innerHTML = `<div style="color:var(--danger);font-weight:900;margin-top:6px;">Error: ${e.message || e}</div>`;
    }
  });

  $('btnRefreshTx').addEventListener('click', async () => {
    try { await refreshDashboard(); } catch {}
  });

  $('btnOpenScanner').addEventListener('click', () => openScannerModal());
  $('btnCloseScanner').addEventListener('click', () => closeScannerModal());

  $('btnVerifyPastedQr').addEventListener('click', async () => {
    const qrText = ($('qrPaste').value || '').trim();
    if (!qrText) {
      $('scannerMsg').textContent = 'Paste QR text first.';
      $('scannerMsg').style.color = 'var(--danger)';
      return;
    }
    await verifyQrTextAndGoPay(qrText);
  });

  $('btnManualPay').addEventListener('click', () => showPayScreenWithManual());
  $('btnBackToDashFromPay').addEventListener('click', async () => { showScreen('dashboard'); await refreshDashboard(); });
  $('btnBackToDashFromSuccess').addEventListener('click', async () => { showScreen('dashboard'); await refreshDashboard(); });

  $('manualReceiverCodeName').addEventListener('change', async () => {
    try { await fetchReceiverCoinFromCode(); }
    catch (e) { setMsg($('payMsg'), e.message || String(e)); }
  });

  $('btnPayNow').addEventListener('click', submitPay);
  $('btnStartRun').addEventListener('click', startRun);
  $('btnStopRun').addEventListener('click', stopRun);

  $('scannerModal').addEventListener('click', (e) => { if (e.target === $('scannerModal')) closeScannerModal(); });
  const aboutModal = $('aboutModal');
  if (aboutModal) aboutModal.addEventListener('click', (e) => { if (e.target === aboutModal) closeAboutModal(); });
}

// ── RUN FEATURE ────────────────────────────────────────────────────────────

function haversineKm(lat1, lon1, lat2, lon2) {
  const R = 6371;
  const dLat = (lat2 - lat1) * Math.PI / 180;
  const dLon = (lon2 - lon1) * Math.PI / 180;
  const a = Math.sin(dLat/2)**2 + Math.cos(lat1*Math.PI/180)*Math.cos(lat2*Math.PI/180)*Math.sin(dLon/2)**2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function getISTMinutes() {
  const now = new Date();
  const utcMs = now.getTime() + now.getTimezoneOffset() * 60000;
  const istMs = utcMs + (5 * 60 + 30) * 60000;
  const d = new Date(istMs);
  return d.getHours() * 60 + d.getMinutes();
}

function updateRunUI() {
  const r = state.run;
  $('runDistance').textContent = r.totalKm.toFixed(2) + ' km';
  $('runSpeed').textContent = r.currentSpeed.toFixed(1) + ' km/h';
  $('runPoints').textContent = r.points.length;
  const remaining = Math.max(0, 10 - r.totalKm);
  $('runStatus').textContent = r.totalKm >= 10
    ? '10 km done! Stop to claim 1 coin.'
    : remaining.toFixed(2) + ' km more needed (no carry-forward)';
}

function startRun() {
  if (!navigator.geolocation) {
    $('runMsg').textContent = 'GPS not supported on this device.';
    $('runMsg').style.color = 'var(--danger)';
    return;
  }
  const istMinutes = getISTMinutes();
  const before5am = istMinutes < 5 * 60;
  const after8am = istMinutes >= 8 * 60;
  if (after8am) {
    $('runMsg').textContent = 'Running window closed (past 8:00 AM IST). Come back before 5:00 AM tomorrow.';
    $('runMsg').style.color = 'var(--danger)';
    return;
  }
  state.run = { active: true, watchId: null, points: [], lastPos: null, totalKm: 0, currentSpeed: 0 };
  $('btnStartRun').classList.add('hidden');
  $('btnStopRun').classList.remove('hidden');
  $('runStats').classList.remove('hidden');
  if (before5am) {
    $('runMsg').textContent = 'GPS tracking started. Run window: 5:00 AM - 8:00 AM IST.';
    $('runMsg').style.color = 'var(--ok)';
  } else {
    $('runMsg').textContent = 'Running (5-8 AM IST window). Server will validate timing.';
    $('runMsg').style.color = 'var(--gold2)';
  }
  updateRunUI();
  state.run.watchId = navigator.geolocation.watchPosition(
    (pos) => {
      if (!state.run.active) return;
      const { latitude: lat, longitude: lon, accuracy } = pos.coords;
      const timestamp = pos.timestamp / 1000;
      const curMin = getISTMinutes();
      if (curMin >= 8 * 60) { stopRun(); $('runMsg').textContent = 'Run window ended at 8:00 AM IST. Submitting...'; return; }
      const point = { lat, lon, timestamp, accuracy_m: accuracy };
      if (state.run.lastPos) {
        const segKm = haversineKm(state.run.lastPos.lat, state.run.lastPos.lon, lat, lon);
        const dtHours = (timestamp - state.run.lastPos.timestamp) / 3600;
        const spd = dtHours > 0 ? segKm / dtHours : 0;
        state.run.currentSpeed = spd;
        if (spd >= 3 && spd <= 20) state.run.totalKm += segKm;
      }
      state.run.points.push(point);
      state.run.lastPos = { lat, lon, timestamp };
      updateRunUI();
    },
    (err) => {
      $('runMsg').textContent = 'GPS error: ' + err.message + '. Allow location access.';
      $('runMsg').style.color = 'var(--danger)';
    },
    { enableHighAccuracy: true, maximumAge: 3000, timeout: 15000 }
  );
}

function stopRun() {
  if (state.run.watchId !== null) { navigator.geolocation.clearWatch(state.run.watchId); state.run.watchId = null; }
  state.run.active = false;
  $('btnStopRun').classList.add('hidden');
  $('btnStartRun').classList.remove('hidden');
  const points = state.run.points;
  if (points.length < 2) {
    $('runMsg').textContent = 'Not enough GPS data recorded.';
    $('runMsg').style.color = 'var(--danger)';
    return;
  }
  $('runMsg').textContent = 'Submitting run to server...';
  $('runMsg').style.color = 'var(--muted)';
  api('/api/run/earn', 'POST', { gps_points: points })
    .then((res) => {
      const earned = res.coins_earned || 0;
      const rupeeVal = (earned * 10000).toLocaleString('en-IN');
      $('runMsg').textContent = res.message || `Earned ${earned} coin(s) worth ₹${rupeeVal}!`;
      $('runMsg').style.color = earned > 0 ? 'var(--ok)' : 'var(--gold2)';
      $('runStats').classList.add('hidden');
      refreshDashboard().catch(() => {});
    })
    .catch((e) => {
      $('runMsg').textContent = 'Run rejected: ' + (e.message || e);
      $('runMsg').style.color = 'var(--danger)';
    });
}

// ── BOOT ──────────────────────────────────────────────────────────────────

function boot() {
  screens = {
    auth:       $('screen-auth'),
    dashboard:  $('screen-dashboard'),
    pay:        $('screen-pay'),
    paySuccess: $('screen-paySuccess'),
  };
  enforceUppercaseInputs();
  bindEvents();
  initSplash();
  initBuyCoins();
  showSplash(true);
  // Show auth initially hidden, splash shows
  Object.values(screens).forEach(el => el && el.classList.add('hidden'));
  checkLoginOnLoad();
}

// ── PARTICLE ANIMATION ─────────────────────────────────────────────────────

function bootParticles() {
  const canvas = $('authParticles');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  let W, H, particles;

  function resize() { W = canvas.width = canvas.offsetWidth; H = canvas.height = canvas.offsetHeight; }
  function mkParticle() {
    return { x: Math.random() * W, y: Math.random() * H, r: Math.random() * 1.5 + 0.4,
      vx: (Math.random() - 0.5) * 0.4, vy: -(Math.random() * 0.5 + 0.2),
      life: Math.random(), maxLife: Math.random() * 0.5 + 0.5 };
  }
  function init() { resize(); particles = Array.from({ length: 55 }, mkParticle); }
  function draw() {
    ctx.clearRect(0, 0, W, H);
    particles.forEach((p, i) => {
      p.x += p.vx; p.y += p.vy; p.life += 0.004;
      if (p.life > p.maxLife || p.y < 0) particles[i] = mkParticle();
      const alpha = Math.sin((p.life / p.maxLife) * Math.PI) * 0.6;
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(212,175,55,${alpha})`;
      ctx.fill();
    });
    requestAnimationFrame(draw);
  }
  init(); draw();
  window.addEventListener('resize', () => { resize(); });
}

window.addEventListener('DOMContentLoaded', () => { boot(); bootParticles(); });
