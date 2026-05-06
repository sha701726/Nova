const $ = (id) => document.getElementById(id);

function togglePw(inputId, btn) {
  const input = $(inputId);
  if (!input) return;
  const isHidden = input.type === 'password';
  input.type = isHidden ? 'text' : 'password';
  btn.textContent = isHidden ? '🙈' : '👁';
}

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

async function boot() {
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

  // Load config from server and initialize Google Sign-In with correct client_id
  try {
    const cfg = await api('/api/config', 'GET');
    if (cfg && cfg.google_client_id) {
      const onloadDiv = document.getElementById('g_id_onload');
      if (onloadDiv) {
        onloadDiv.setAttribute('data-client_id', cfg.google_client_id);
      }
      // Re-initialize Google Identity Services with the correct client_id
      if (window.google && window.google.accounts && window.google.accounts.id) {
        window.google.accounts.id.initialize({
          client_id: cfg.google_client_id,
          callback: window.handleGoogleCredential,
          auto_select: false,
        });
        const btnContainer = document.querySelector('.g_id_signin');
        if (btnContainer) {
          window.google.accounts.id.renderButton(btnContainer, {
            type: 'standard',
            size: 'large',
            theme: 'filled_black',
            text: 'sign_in_with',
            shape: 'rectangular',
            logo_alignment: 'left',
            width: btnContainer.offsetWidth || 300,
          });
        }
      }
    }
  } catch (e) {
    console.warn('Config load failed:', e);
  }

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

// ═══════════════════════════════════════════════════════════════════════════════
// NOVA WATCH STORE — STORE LOGIC
// All original Nova code is untouched above.
// ═══════════════════════════════════════════════════════════════════════════════

const STORE_ADMIN = 'SHADOW'; // Must match STORE_ADMIN_CODE_NAME in app.py
const RZP_KEY     = 'rzp_test_SiOpSGdjiLhfLU';

let storeData = { products: [], settings: {}, filtered: [] };

// ── Helpers ───────────────────────────────────────────────────────────────────

function storeApi(path, method = 'GET', body) {
  return api(path, method, body);
}

function isStoreAdmin() {
  return state.me && state.me.code_name &&
         state.me.code_name.toUpperCase() === STORE_ADMIN.toUpperCase();
}

function showStoreMsg(elId, msg, kind = 'error') {
  const el = $(elId);
  if (!el) return;
  el.textContent = msg;
  el.style.color = kind === 'ok' ? 'var(--ok)' : 'var(--danger)';
  el.style.display = 'block';
}

// ── Navigate to store ─────────────────────────────────────────────────────────

async function openStore() {
  showScreen('store');
  $('storeMain').style.display = 'block';
  $('storeDetail').style.display = 'none';
  // Show admin button if admin
  const adminBtn = $('btnAdminPanel');
  if (adminBtn) adminBtn.style.display = isStoreAdmin() ? 'block' : 'none';
  await loadStoreProducts();
}

async function loadStoreProducts() {
  try {
    const res = await storeApi('/api/store/products');
    storeData.products = res.products || [];
    storeData.settings = res.settings || {};
    storeData.filtered = storeData.products;
    const rate = parseInt(storeData.settings.coin_to_inr || 10000);
    const rateEl = $('storeCoinRate');
    if (rateEl) rateEl.textContent = rate.toLocaleString('en-IN');
    renderStoreGrid(storeData.filtered);
  } catch (e) {
    $('storeProductGrid').innerHTML = `<div class="muted small" style="padding:20px 0;">Failed to load products: ${e.message||e}</div>`;
  }
}

function renderStoreGrid(products) {
  const grid = $('storeProductGrid');
  if (!products.length) {
    grid.innerHTML = '<div class="muted small" style="padding:20px 0;text-align:center;">No products yet. Check back soon.</div>';
    return;
  }
  const html = `<div class="store-grid">${products.map(p => {
    const sc = p.stock === 0 ? 'out' : p.stock < 5 ? 'low' : '';
    const st = p.stock === 0 ? 'Out of Stock' : p.stock < 5 ? `Only ${p.stock} left` : 'In Stock';
    const imgHtml = p.image_url
      ? `<div class="store-card-img"><img src="${p.image_url}" alt="${p.name}" onerror="this.parentElement.innerHTML='⌚'"/></div>`
      : `<div class="store-card-img">⌚</div>`;
    return `<div class="store-card" onclick="openProductDetail(${p.id})">
      ${imgHtml}
      <div class="store-card-body">
        <div class="store-card-cat">${p.category}</div>
        <div class="store-card-name">${p.name}</div>
        <div class="store-card-price-coin">◎ ${p.price_coins}</div>
        <div class="store-card-price-inr">₹${Number(p.price_inr).toLocaleString('en-IN')}</div>
        <div class="store-card-stock ${sc}">${st}</div>
      </div>
    </div>`;
  }).join('')}</div>`;
  grid.innerHTML = html;
}

function filterStoreProducts(cat) {
  storeData.filtered = cat === 'all'
    ? storeData.products
    : storeData.products.filter(p => p.category === cat);
  renderStoreGrid(storeData.filtered);
}

// ── Product detail ────────────────────────────────────────────────────────────

function openProductDetail(id) {
  const p = storeData.products.find(x => x.id === id);
  if (!p) return;
  $('storeMain').style.display = 'none';
  $('storeDetail').style.display = 'block';

  const svc_c = parseFloat(storeData.settings.service_charge_coins || 0.05);
  const svc_i = parseInt(storeData.settings.service_charge_inr || 500);
  const total_c = (p.price_coins + svc_c).toFixed(4);
  const total_i = p.price_inr + svc_i;
  const ce = storeData.settings.coins_enabled !== '0';
  const re = storeData.settings.razorpay_enabled !== '0';
  const oos = p.stock === 0;
  const sc = p.stock === 0 ? 'out' : p.stock < 5 ? 'low' : '';
  const st = p.stock === 0 ? 'Out of Stock' : p.stock < 5 ? `Only ${p.stock} left` : `${p.stock} in stock`;

  const feats = p.features
    ? p.features.split('\n').filter(f => f.trim())
        .map(f => `<div class="store-detail-feat-item">${f.trim()}</div>`).join('')
    : '';

  const imgHtml = p.image_url
    ? `<div class="store-detail-img"><img src="${p.image_url}" alt="${p.name}" onerror="this.parentElement.innerHTML='⌚'"/></div>`
    : `<div class="store-detail-img">⌚</div>`;

  const balLine = state.me ? `<div class="muted small">Your balance: ◎${state.me.coinvalue}</div>` : '';

  $('storeDetailContent').innerHTML = `
    ${imgHtml}
    <div class="store-detail-cat">${p.category}</div>
    <div class="store-detail-name">${p.name}</div>
    <div class="store-detail-desc">${p.description || 'A precision timepiece from the Nova collection.'}</div>
    ${feats ? `<div class="store-detail-features"><div class="muted small" style="margin-bottom:6px;letter-spacing:.1em;text-transform:uppercase;">Specifications</div>${feats}</div>` : ''}
    <div class="store-price-box">
      <div class="store-price-row"><span class="store-price-label">Nova Coins</span><span class="store-price-val gold">◎ ${p.price_coins}</span></div>
      <div class="store-price-row"><span class="store-price-label">Razorpay (INR)</span><span class="store-price-val">₹${Number(p.price_inr).toLocaleString('en-IN')}</span></div>
      <div class="muted small" style="margin-top:6px;">+ Service: ◎${svc_c} / ₹${svc_i.toLocaleString('en-IN')}</div>
    </div>
    <div class="store-card-stock ${sc}" style="margin-bottom:12px;font-size:12px;">${st}</div>
    ${oos ? `<div class="muted small" style="text-align:center;padding:16px 0;">This product is currently out of stock.</div>` : `
      ${!state.me ? `<button class="btn btn-gold btn-full" onclick="showTab('login');showScreen('auth');">Login to Buy</button>` : `
        <div class="buy-method-tabs">
          ${ce ? `<button class="buy-method-tab active" data-method="coin" onclick="switchBuyMethod('coin',this)">◎ Coins</button>` : ''}
          ${re ? `<button class="buy-method-tab ${!ce?'active':''}" data-method="rz" onclick="switchBuyMethod('rz',this)">💳 Razorpay</button>` : ''}
        </div>
        ${ce ? `<div class="buy-method-panel active" id="buy-panel-coin">
          <div style="background:rgba(212,175,55,0.07);border:1px solid rgba(212,175,55,0.15);border-radius:6px;padding:10px;margin-bottom:10px;font-size:13px;">
            Total: <strong style="color:var(--gold2)">◎${total_c}</strong> (incl. ◎${svc_c} service)
            ${balLine}
          </div>
          <div class="ship-title">Delivery Information</div>
          <div class="field"><input id="coinShipName" class="field-input" placeholder="Full Name"/></div>
          <div class="field"><input id="coinShipPhone" class="field-input" placeholder="Phone Number"/></div>
          <div class="field"><textarea id="coinShipAddr" rows="2" class="field-input" placeholder="Complete delivery address"></textarea></div>
          <div class="field"><label>Your Password (to confirm)</label><div class="pw-wrap"><input id="coinBuyPass" type="password" class="field-input" placeholder="Enter password"/><button type="button" class="pw-eye" onclick="togglePw('coinBuyPass', this)" tabindex="-1" aria-label="Show/hide password">&#x1F441;</button></div></div>
          <button class="btn btn-gold btn-full" style="margin-top:10px;" onclick="buyWithCoins(${p.id},${total_c})">Confirm — ◎${total_c}</button>
          <div id="coinBuyMsg" class="msg"></div>
        </div>` : ''}
        ${re ? `<div class="buy-method-panel ${!ce?'active':''}" id="buy-panel-rz">
          <div style="background:rgba(212,175,55,0.07);border:1px solid rgba(212,175,55,0.15);border-radius:6px;padding:10px;margin-bottom:10px;font-size:13px;">
            Total: <strong>₹${Number(total_i).toLocaleString('en-IN')}</strong> (incl. ₹${svc_i.toLocaleString('en-IN')} service)
          </div>
          <div class="ship-title">Delivery Information</div>
          <div class="field"><input id="rzShipName" class="field-input" placeholder="Full Name"/></div>
          <div class="field"><input id="rzShipPhone" class="field-input" placeholder="Phone Number"/></div>
          <div class="field"><textarea id="rzShipAddr" rows="2" class="field-input" placeholder="Complete delivery address"></textarea></div>
          <button class="btn btn-gold btn-full" style="margin-top:10px;" onclick="buyWithRazorpay(${p.id},${total_i})">Pay ₹${Number(total_i).toLocaleString('en-IN')} via Razorpay</button>
          <div id="rzBuyMsg" class="msg"></div>
        </div>` : ''}
      `}
    `}
  `;
}

function switchBuyMethod(method, btn) {
  document.querySelectorAll('.buy-method-tab').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.buy-method-panel').forEach(p => p.classList.remove('active'));
  btn.classList.add('active');
  const panel = $('buy-panel-' + method);
  if (panel) panel.classList.add('active');
}

// ── Buy with Coins ────────────────────────────────────────────────────────────

async function buyWithCoins(productId, totalCoins) {
  const pass    = ($('coinBuyPass')   ? $('coinBuyPass').value   : '').trim();
  const name    = ($('coinShipName')  ? $('coinShipName').value  : '').trim();
  const phone   = ($('coinShipPhone') ? $('coinShipPhone').value : '').trim();
  const address = ($('coinShipAddr')  ? $('coinShipAddr').value  : '').trim();
  const msgEl   = $('coinBuyMsg');

  if (!pass)    { setMsg(msgEl, 'Password required');        return; }
  if (!name)    { setMsg(msgEl, 'Full name required');       return; }
  if (!address) { setMsg(msgEl, 'Delivery address required'); return; }
  if (!state.me || state.me.coinvalue < totalCoins) {
    setMsg(msgEl, `Insufficient coins. Need ◎${totalCoins}, have ◎${state.me?.coinvalue||0}`); return;
  }

  setMsg(msgEl, 'Processing...', 'ok');
  try {
    const res = await storeApi('/api/store/order/coin', 'POST', {
      product_id: productId, password: pass,
      shipping: { name, phone, address }
    });
    state.me.coinvalue = res.new_balance;
    const _rate = parseInt(storeData.settings.coin_to_inr || 10000);
    const _rupees = (res.new_balance * _rate).toLocaleString('en-IN');
    const _coinDisp = `${res.new_balance} (₹${_rupees})`;
    if ($('meCoinValue'))        $('meCoinValue').textContent        = _coinDisp;
    if ($('sidebarMeCoinValue')) $('sidebarMeCoinValue').textContent = _coinDisp;
    setMsg(msgEl, `✓ Order placed! #${res.order_no} — ◎${res.coins_spent} spent`, 'ok');
    await loadStoreProducts();
  } catch (e) {
    setMsg(msgEl, e.message || 'Order failed');
  }
}

// ── Buy with Razorpay ─────────────────────────────────────────────────────────

async function buyWithRazorpay(productId, totalInr) {
  const name    = ($('rzShipName')  ? $('rzShipName').value  : '').trim();
  const phone   = ($('rzShipPhone') ? $('rzShipPhone').value : '').trim();
  const address = ($('rzShipAddr')  ? $('rzShipAddr').value  : '').trim();
  const msgEl   = $('rzBuyMsg');

  if (!name)    { setMsg(msgEl, 'Full name required');        return; }
  if (!address) { setMsg(msgEl, 'Delivery address required'); return; }

  setMsg(msgEl, 'Creating order...', 'ok');
  try {
    const order = await storeApi('/api/store/order/razorpay/create', 'POST', { product_id: productId });
    const options = {
      key: RZP_KEY,
      amount: order.amount,
      currency: order.currency,
      name: 'Nova Watch Store',
      description: order.product_name,
      order_id: order.razorpay_order_id,
      handler: async (resp) => {
        setMsg(msgEl, 'Verifying payment...', 'ok');
        try {
          const vRes = await storeApi('/api/store/order/razorpay/verify', 'POST', {
            razorpay_order_id: resp.razorpay_order_id,
            razorpay_payment_id: resp.razorpay_payment_id,
            razorpay_signature: resp.razorpay_signature,
            order_no: order.order_no,
            shipping: { name, phone, address }
          });
          setMsg(msgEl, `Payment confirmed! Order #${vRes.order_no}`, 'ok');
          await loadStoreProducts();
        } catch (e2) {
          setMsg(msgEl, 'Verification failed: ' + (e2.message || e2));
        }
      },
      prefill: { name: state.me?.code_name || '', contact: phone },
      theme: { color: '#d4af37' },
      modal: { ondismiss: () => setMsg(msgEl, 'Payment cancelled.') }
    };
    const rzp = new Razorpay(options);
    rzp.on('payment.failed', (r) => setMsg(msgEl, 'Payment failed: ' + (r.error?.description || 'Unknown')));
    setMsg(msgEl, '');
    rzp.open();
  } catch (e) {
    setMsg(msgEl, 'Could not initiate payment: ' + (e.message || e));
  }
}

// ── My Orders Modal ───────────────────────────────────────────────────────────

async function openMyOrdersModal() {
  const modal = $('myOrdersModal');
  if (!modal) return;
  modal.classList.remove('hidden');
  $('myOrdersList').innerHTML = '<div class="muted small" style="padding:16px 0;">Loading...</div>';
  try {
    const res = await storeApi('/api/store/my-orders');
    const orders = res.orders || [];
    if (!orders.length) {
      $('myOrdersList').innerHTML = '<div class="muted small" style="padding:16px 0;text-align:center;">No orders yet.</div>';
      return;
    }
    $('myOrdersList').innerHTML = orders.map(o => `
      <div class="my-order-item">
        <div class="my-order-no">#${o.order_no}</div>
        <div class="my-order-name">${o.product_name}</div>
        <div class="my-order-meta">
          ${o.payment_method === 'COIN' ? `◎${o.coins_spent}` : `₹${Number(o.inr_paid/100).toLocaleString('en-IN')}`}
          &nbsp;<span class="status-pill ${o.status}">${o.status}</span>
        </div>
        <div class="my-order-meta">${o.created_at}</div>
      </div>`).join('');
  } catch (e) {
    $('myOrdersList').innerHTML = `<div style="color:var(--danger)">Error: ${e.message||e}</div>`;
  }
}

// ── Admin ─────────────────────────────────────────────────────────────────────

function openAdminPanel() {
  if (!isStoreAdmin()) return;
  showScreen('admin');
  loadAdminProducts();
  loadAdminSettings();
}

function switchAdminTab(tab) {
  document.querySelectorAll('.admin-tab-panel').forEach(p => p.style.display = 'none');
  document.querySelectorAll('.admin-tab-btn').forEach(b => {
    b.classList.remove('active');
    b.style.borderBottom = 'none';
  });
  const panel = $('admin-tab-' + tab);
  if (panel) panel.style.display = 'block';
  const btn = document.querySelector(`.admin-tab-btn[data-atab="${tab}"]`);
  if (btn) { btn.classList.add('active'); btn.style.borderBottom = '2px solid var(--gold2)'; }

  if (tab === 'orders') loadAdminOrders();
  if (tab === 'users')  loadAdminUsers();
}

async function loadAdminProducts() {
  const el = $('adminProductList');
  el.innerHTML = '<div class="muted small">Loading...</div>';
  try {
    const res = await storeApi('/api/admin/products');
    const prods = res.products || [];
    if (!prods.length) { el.innerHTML = '<div class="muted small">No products. Click + Add to create one.</div>'; return; }
    el.innerHTML = prods.map(p => `
      <div class="admin-product-row">
        <div class="admin-product-thumb">
          ${p.image_url ? `<img src="${p.image_url}" alt="${p.name}" onerror="this.parentElement.innerHTML='⌚'"/>` : '⌚'}
        </div>
        <div class="admin-product-info">
          <div class="admin-product-name">${p.name}</div>
          <div class="admin-product-meta">◎${p.price_coins} / ₹${Number(p.price_inr).toLocaleString('en-IN')} · Stock: ${p.stock} · ${p.active ? '<span style="color:var(--ok)">Active</span>' : '<span style="color:var(--danger)">Hidden</span>'}</div>
        </div>
        <div class="admin-product-actions">
          <button class="btn btn-ghost btn-sm" onclick='openProductEditModal(${JSON.stringify(p)})'>Edit</button>
          <button class="btn btn-ghost btn-sm" style="color:var(--danger)" onclick="adminDeleteProduct(${p.id},'${p.name.replace(/'/g,"\\'")}')">Del</button>
        </div>
      </div>`).join('');
  } catch (e) {
    el.innerHTML = `<div style="color:var(--danger)">${e.message||e}</div>`;
  }
}

async function loadAdminOrders() {
  const el = $('adminOrderList');
  el.innerHTML = '<div class="muted small">Loading...</div>';
  try {
    const res = await storeApi('/api/admin/orders');
    const orders = res.orders || [];
    if (!orders.length) { el.innerHTML = '<div class="muted small">No orders yet.</div>'; return; }
    el.innerHTML = orders.map(o => `
      <div class="admin-order-row">
        <div class="admin-order-no">#${o.order_no}</div>
        <div style="font-size:13px;font-weight:700;">${o.product_name}</div>
        <div class="admin-order-meta">
          ${o.code_name} · ${o.payment_method === 'COIN' ? `◎${o.coins_spent}` : `₹${Number(o.inr_paid/100).toLocaleString('en-IN')}`}
          &nbsp;<span class="status-pill ${o.status}">${o.status}</span>
        </div>
        <div class="admin-order-meta">${o.shipping_name || '—'} · ${o.shipping_phone || ''}</div>
        <div style="margin-top:6px;display:flex;align-items:center;gap:8px;">
          <select class="admin-status-select" onchange="adminUpdateOrder('${o.order_no}',this.value)">
            ${['PENDING','CONFIRMED','SHIPPED','DELIVERED','CANCELLED'].map(s => `<option value="${s}" ${s===o.status?'selected':''}>${s}</option>`).join('')}
          </select>
          <span class="muted small">${o.created_at ? o.created_at.slice(0,16) : ''}</span>
        </div>
      </div>`).join('');
  } catch (e) {
    el.innerHTML = `<div style="color:var(--danger)">${e.message||e}</div>`;
  }
}

async function loadAdminUsers() {
  const el = $('adminUserList');
  el.innerHTML = '<div class="muted small">Loading...</div>';
  try {
    const res = await storeApi('/api/admin/users');
    const users = res.users || [];
    if (!users.length) { el.innerHTML = '<div class="muted small">No users.</div>'; return; }
    el.innerHTML = users.map(u => `
      <div class="admin-order-row">
        <div style="font-family:monospace;color:var(--gold2);font-size:12px;">${u.code_name}</div>
        <div style="font-size:13px;">${u.full_name || '—'}</div>
        <div class="admin-order-meta">${u.email||'—'} · ${u.phone||'—'}</div>
        <div class="admin-order-meta">Balance: <strong style="color:var(--gold2)">◎${u.balance}</strong> · Joined: ${u.created_at ? u.created_at.slice(0,10) : ''}</div>
      </div>`).join('');
  } catch (e) {
    el.innerHTML = `<div style="color:var(--danger)">${e.message||e}</div>`;
  }
}

async function loadAdminSettings() {
  try {
    const res = await storeApi('/api/admin/settings');
    const s = res.settings || {};
    ['coin_to_inr','service_charge_inr','service_charge_coins','store_name','razorpay_enabled','coins_enabled'].forEach(k => {
      const el = $('set-' + k);
      if (el && s[k] !== undefined) el.value = s[k];
    });
  } catch (e) {}
}

async function adminUpdateOrder(orderNo, status) {
  try {
    await storeApi(`/api/admin/order/${orderNo}`, 'PUT', { status });
  } catch (e) {
    alert('Update failed: ' + (e.message || e));
  }
}

async function adminDeleteProduct(id, name) {
  if (!confirm(`Delete "${name}"? This cannot be undone.`)) return;
  try {
    await storeApi(`/api/admin/product/${id}`, 'DELETE');
    await loadAdminProducts();
  } catch (e) {
    alert('Delete failed: ' + (e.message || e));
  }
}

// ── Product Edit Modal ────────────────────────────────────────────────────────

function openProductEditModal(product) {
  const modal = $('productEditModal');
  $('productEditTitle').textContent = product && product.id ? 'Edit Product' : 'Add Product';
  $('editProductId').value = product && product.id ? product.id : '';
  $('editName').value         = product ? product.name        || '' : '';
  $('editCategory').value     = product ? product.category    || 'watches' : 'watches';
  $('editPriceCoins').value   = product ? product.price_coins || 1.0 : 1.0;
  $('editPriceInr').value     = product ? product.price_inr   || 10000 : 10000;
  $('editStock').value        = product ? product.stock        || 10 : 10;
  $('editImageUrl').value     = product ? product.image_url   || '' : '';
  $('editDescription').value  = product ? product.description || '' : '';
  $('editFeatures').value     = product ? product.features    || '' : '';
  $('editActive').value       = product && !product.active ? '0' : '1';
  $('productEditMsg').textContent = '';
  $('productEditMsg').style.display = 'none';
  modal.classList.remove('hidden');
}

async function saveProduct() {
  const id = $('editProductId').value;
  const payload = {
    name:        ($('editName').value || '').trim(),
    category:    $('editCategory').value,
    price_coins: parseFloat($('editPriceCoins').value) || 1.0,
    price_inr:   parseInt($('editPriceInr').value)     || 10000,
    stock:       parseInt($('editStock').value)         || 0,
    image_url:   ($('editImageUrl').value    || '').trim(),
    description: ($('editDescription').value || '').trim(),
    features:    ($('editFeatures').value    || '').trim(),
    active:      parseInt($('editActive').value),
  };
  if (!payload.name) { showStoreMsg('productEditMsg', 'Name is required'); return; }
  try {
    const url    = id ? `/api/admin/product/${id}` : '/api/admin/product';
    const method = id ? 'PUT' : 'POST';
    await storeApi(url, method, payload);
    $('productEditModal').classList.add('hidden');
    await loadAdminProducts();
    await loadStoreProducts();
  } catch (e) {
    showStoreMsg('productEditMsg', e.message || 'Save failed');
  }
}

// ── Delete Account Modal ──────────────────────────────────────────────────────

function openDeleteAccountModal() {
  if (!state.me) return;
  const modal = $('deleteAccountModal');
  $('deleteConfirmCode').value = '';
  $('deleteConfirmPass').value = '';
  $('deleteFinalCheck').checked = false;
  $('deleteModalMsg').style.display = 'none';
  $('deleteCodeHint').textContent = 'Must match exactly';
  $('deleteCodeHint').className = '';
  $('btnConfirmDelete').disabled = true;
  $('btnConfirmDelete').style.opacity = '0.4';
  $('btnConfirmDelete').style.cursor  = 'not-allowed';

  const bal = state.me.coinvalue;
  const warn = $('deleteBalanceWarn');
  warn.innerHTML = bal > 0
    ? `<span style="color:var(--danger)">⚠ You have ◎${bal} coins. Transfer or spend all coins before deleting.</span>`
    : `<span style="color:var(--ok)">✓ Balance is ◎0 — eligible for deletion.</span>`;

  modal.classList.remove('hidden');
}

function checkDeleteReady() {
  const code    = ($('deleteConfirmCode').value || '').trim().toUpperCase();
  const pass    = ($('deleteConfirmPass').value || '').trim();
  const checked = $('deleteFinalCheck').checked;
  const hint    = $('deleteCodeHint');
  const btn     = $('btnConfirmDelete');
  const myCode  = state.me ? state.me.code_name.toUpperCase() : '';

  if (code.length > 0) {
    if (code === myCode) {
      hint.textContent = '✓ Code name matches';
      hint.className = 'hint-ok';
    } else {
      hint.textContent = '✗ Does not match your code name';
      hint.className = 'hint-bad';
    }
  } else {
    hint.textContent = 'Must match exactly';
    hint.className = '';
  }

  const ready = (code === myCode) && pass.length >= 12 && checked;
  btn.disabled = !ready;
  btn.style.opacity = ready ? '1' : '0.4';
  btn.style.cursor  = ready ? 'pointer' : 'not-allowed';
}

async function confirmDeleteAccount() {
  const code    = ($('deleteConfirmCode').value || '').trim().toUpperCase();
  const pass    = ($('deleteConfirmPass').value || '').trim();
  const checked = $('deleteFinalCheck').checked;
  const msgEl   = $('deleteModalMsg');
  const myCode  = state.me ? state.me.code_name.toUpperCase() : '';

  if (code !== myCode)         { msgEl.textContent = 'Code name does not match.'; msgEl.style.color = 'var(--danger)'; msgEl.style.display = 'block'; return; }
  if (!pass)                   { msgEl.textContent = 'Password is required.';     msgEl.style.color = 'var(--danger)'; msgEl.style.display = 'block'; return; }
  if (!checked)                { msgEl.textContent = 'Please tick the checkbox.'; msgEl.style.color = 'var(--danger)'; msgEl.style.display = 'block'; return; }

  const btn = $('btnConfirmDelete');
  btn.disabled = true; btn.textContent = 'Deleting...';

  try {
    await storeApi('/api/account/delete', 'DELETE', { password: pass });
    $('deleteAccountModal').classList.add('hidden');
    state.me = null; state.receiver = null;
    showScreen('auth');
    $('loginStatus').textContent = 'Account deleted.';
  } catch (e) {
    msgEl.textContent = e.message || 'Deletion failed.';
    msgEl.style.color = 'var(--danger)';
    msgEl.style.display = 'block';
    btn.disabled = false;
    btn.textContent = 'Delete Permanently';
  }
}

// ── Wire up all store events when DOM loads ───────────────────────────────────

function bindStoreEvents() {
  // Store button on dashboard
  const btnStore = $('btnStore');
  if (btnStore) btnStore.addEventListener('click', () => openStore());

  // Back from store
  const btnBack = $('btnBackToDashFromStore');
  if (btnBack) btnBack.addEventListener('click', async () => { showScreen('dashboard'); await refreshDashboard(); });

  // Back to store grid from detail
  const btnBackStore = $('btnBackToStore');
  if (btnBackStore) btnBackStore.addEventListener('click', () => {
    $('storeMain').style.display = 'block';
    $('storeDetail').style.display = 'none';
  });

  // Admin panel button
  const btnAdmin = $('btnAdminPanel');
  if (btnAdmin) btnAdmin.addEventListener('click', () => openAdminPanel());

  // Back from admin
  const btnBackAdmin = $('btnBackFromAdmin');
  if (btnBackAdmin) btnBackAdmin.addEventListener('click', () => openStore());

  // Admin tabs
  document.querySelectorAll('.admin-tab-btn').forEach(btn => {
    btn.addEventListener('click', () => switchAdminTab(btn.dataset.atab));
  });

  // Add product
  const btnAddProd = $('btnAddProduct');
  if (btnAddProd) btnAddProd.addEventListener('click', () => openProductEditModal(null));

  // Save product
  const btnSaveProd = $('btnSaveProduct');
  if (btnSaveProd) btnSaveProd.addEventListener('click', saveProduct);

  // Cancel product edit
  const btnCancelEdit = $('btnCancelProductEdit');
  if (btnCancelEdit) btnCancelEdit.addEventListener('click', () => $('productEditModal').classList.add('hidden'));
  const btnCloseEdit = $('btnCloseProductEdit');
  if (btnCloseEdit) btnCloseEdit.addEventListener('click', () => $('productEditModal').classList.add('hidden'));

  // Refresh admin orders / users
  const btnRefOrders = $('btnRefreshOrders');
  if (btnRefOrders) btnRefOrders.addEventListener('click', loadAdminOrders);
  const btnRefUsers = $('btnRefreshUsers');
  if (btnRefUsers) btnRefUsers.addEventListener('click', loadAdminUsers);

  // Save settings
  const btnSaveSet = $('btnSaveSettings');
  if (btnSaveSet) btnSaveSet.addEventListener('click', async () => {
    const keys = ['coin_to_inr','service_charge_inr','service_charge_coins','store_name','razorpay_enabled','coins_enabled'];
    const payload = {};
    keys.forEach(k => { const el = $('set-' + k); if (el) payload[k] = el.value; });
    try {
      await storeApi('/api/admin/settings', 'POST', payload);
      showStoreMsg('settingsMsg', 'Settings saved!', 'ok');
      await loadStoreProducts();
    } catch (e) {
      showStoreMsg('settingsMsg', e.message || 'Save failed');
    }
  });

  // Category filter
  const catBtns = $('storeCatBtns');
  if (catBtns) {
    catBtns.addEventListener('click', e => {
      const btn = e.target.closest('.store-cat-btn');
      if (!btn) return;
      catBtns.querySelectorAll('.store-cat-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      filterStoreProducts(btn.dataset.cat);
    });
  }

  // My orders button
  const btnMyOrders = $('btnMyOrders');
  if (btnMyOrders) btnMyOrders.addEventListener('click', openMyOrdersModal);
  const btnCloseMyOrders = $('btnCloseMyOrders');
  if (btnCloseMyOrders) btnCloseMyOrders.addEventListener('click', () => $('myOrdersModal').classList.add('hidden'));
  const myOrdersModal = $('myOrdersModal');
  if (myOrdersModal) myOrdersModal.addEventListener('click', e => { if (e.target === myOrdersModal) myOrdersModal.classList.add('hidden'); });

  // Delete account button
  const btnDel = $('btnDeleteAccount');
  if (btnDel) btnDel.addEventListener('click', openDeleteAccountModal);

  // Delete modal events
  const deleteConfirmCode = $('deleteConfirmCode');
  if (deleteConfirmCode) deleteConfirmCode.addEventListener('input', checkDeleteReady);
  const deleteConfirmPass = $('deleteConfirmPass');
  if (deleteConfirmPass) deleteConfirmPass.addEventListener('input', checkDeleteReady);
  const deleteFinalCheck = $('deleteFinalCheck');
  if (deleteFinalCheck) deleteFinalCheck.addEventListener('change', checkDeleteReady);
  const btnConfirmDel = $('btnConfirmDelete');
  if (btnConfirmDel) btnConfirmDel.addEventListener('click', confirmDeleteAccount);
  const btnCancelDel = $('btnCancelDelete');
  if (btnCancelDel) btnCancelDel.addEventListener('click', () => $('deleteAccountModal').classList.add('hidden'));
  const btnCloseDel = $('btnCloseDeleteModal');
  if (btnCloseDel) btnCloseDel.addEventListener('click', () => $('deleteAccountModal').classList.add('hidden'));
  const delModal = $('deleteAccountModal');
  if (delModal) delModal.addEventListener('click', e => { if (e.target === delModal) delModal.classList.add('hidden'); });

  // Product edit modal close on backdrop
  const pEditModal = $('productEditModal');
  if (pEditModal) pEditModal.addEventListener('click', e => { if (e.target === pEditModal) pEditModal.classList.add('hidden'); });
}

// Patch boot() so store screens are registered at the same time as the rest
const _origBoot = boot;
boot = function() {
  _origBoot();
  screens['store'] = $('screen-store');
  screens['admin'] = $('screen-admin');
  // Hide new screens same as the originals
  [$('screen-store'), $('screen-admin')].forEach(el => el && el.classList.add('hidden'));
  bindStoreEvents();
};

// ═══════════════════════════════════════════════════════════════════════════════
// END NOVA WATCH STORE
// ═══════════════════════════════════════════════════════════════════════════════
