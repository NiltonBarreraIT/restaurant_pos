/* =========================================================
   POS.JS (corregido)
   - Fallback si no existe openCashModal/openCashForm
   - paymentMethod UI: "Efectivo"/"Transferencia" -> API: "cash"/"transfer"
   ========================================================= */

let orderItems = [];
let total = 0;
let currentDetailOrderId = null;

/* ================== DOM SAFE GET ================== */
const $ = (id) => document.getElementById(id);

/* UI elements (pueden ser null si cambiaste HTML) */
const productsEl = $("products");
const orderListEl = $("orderList");
const referenceNameEl = $("referenceName");
const paymentMethodEl = $("paymentMethod");
const paidAmountEl = $("paidAmount");
const pedidoTotalEl = $("pedidoTotal");
const pedidoVueltoEl = $("pedidoVuelto");
const submitOrderBtn = $("submitOrder");
const payHint = $("payHint");
const successSound = $("successSound");

/* Caja overlay y contenido */
const cashLockOverlay = $("cashLockOverlay");
const posContent = $("posContent");

const btnOpenCashOverlay = $("btnOpenCashOverlay");
const btnOpenCash = $("btnOpenCash");
const btnCloseCash = $("btnCloseCash");

const cashStatusBadge = $("cashStatusBadge");
const cashStatusInfo = $("cashStatusInfo");

/* Modal (si existe) */
const openCashModal = $("openCashModal");
const openCashForm = $("openCashForm");
const openCashError = $("openCashError");
const openingAmount = $("openingAmount");
const openingNotes = $("openingNotes");

/* Historial */
const historialBody = $("historialBody");

/* Detalle pedido (modal) */
const orderDetailModalEl = $("orderDetailModal");
const detailCustomer = $("detailCustomer");
const detailDate = $("detailDate"); // si no existe, no pasa nada
const detailTotal = $("detailTotal");
const detailItems = $("detailItems");

/* ================== ESTADO CAJA ================== */
let cashIsOpen = false;

/* ================== HELPERS ================== */
function money(n) {
  const num = Number(n || 0);
  return isNaN(num) ? 0 : Math.round(num);
}

// Mapea UI -> API
function getPaymentMethodApi() {
  const v = (paymentMethodEl?.value || "").trim().toLowerCase();

  // Tu HTML actual usa "Efectivo"/"Transferencia"
  if (v === "efectivo") return "cash";
  if (v === "transferencia") return "transfer";

  // Por si en algún momento usas values "cash"/"transfer"
  if (v === "cash" || v === "transfer") return v;

  // default seguro
  return "cash";
}

function isCashPayment() {
  return getPaymentMethodApi() === "cash";
}

/* ================== TOTALES / VALIDACIONES ================== */
function updateTotals() {
  if (pedidoTotalEl) pedidoTotalEl.innerText = money(total);
  updateChange();
  updateProductButtons();
}

function updateChange() {
  if (!submitOrderBtn) return;

  const paid = money(paidAmountEl?.value);
  const isCash = isCashPayment();

  let change = 0;

  if (isCash) {
    if (paid < total) {
      if (payHint) payHint.classList.remove("d-none");
      change = 0;
      submitOrderBtn.disabled = true;
    } else {
      if (payHint) payHint.classList.add("d-none");
      change = paid - total;
    }
  } else {
    if (payHint) payHint.classList.add("d-none");
    change = 0; // transferencia no calcula vuelto
  }

  if (pedidoVueltoEl) pedidoVueltoEl.innerText = money(change);

  // regla final
  submitOrderBtn.disabled = !cashIsOpen || total <= 0 || (isCash && paid < total);
}

/* ================== PEDIDO ================== */
function renderOrder() {
  if (!orderListEl) return;

  orderListEl.innerHTML = "";

  orderItems.forEach((item, index) => {
    const li = document.createElement("li");
    li.className = "list-group-item d-flex justify-content-between align-items-center";

    li.innerHTML = `
      <div>
        <strong>${item.name}</strong><br>
        $${money(item.price)} x ${money(item.qty)} = <strong>$${money(item.price) * money(item.qty)}</strong>
      </div>
      <div>
        <button class="btn btn-sm btn-warning me-1" type="button">−</button>
        <button class="btn btn-sm btn-danger" type="button">❌</button>
      </div>
    `;

    li.querySelector(".btn-warning").onclick = () => {
      item.qty--;
      total -= money(item.price);
      if (item.qty <= 0) orderItems.splice(index, 1);
      renderOrder();
    };

    li.querySelector(".btn-danger").onclick = () => {
      total -= money(item.price) * money(item.qty);
      orderItems.splice(index, 1);
      renderOrder();
    };

    orderListEl.appendChild(li);
  });

  updateTotals();
}

/* ================== PRODUCTOS ================== */
function loadProducts() {
  if (!productsEl) return;

  fetch("/pos/products")
    .then((r) => r.json())
    .then((data) => {
      productsEl.innerHTML = "";

      data.forEach((p) => {
        const col = document.createElement("div");
        col.className = "col-md-4 position-relative";

        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "btn btn-primary w-100 product-btn";
        btn.style.height = "120px";
        btn.style.fontSize = "22px";
        btn.innerText = `${p.name}\n$${money(p.price)}`;
        btn.dataset.id = p.id;

        btn.onclick = () => addProduct({
          id: p.id,
          name: p.name,
          price: money(p.price)
        });

        col.appendChild(btn);
        productsEl.appendChild(col);
      });

      updateProductButtons();
    })
    .catch((e) => console.error("Error productos:", e));
}

function updateProductButtons() {
  document.querySelectorAll(".product-btn").forEach((btn) => {
    const id = btn.dataset.id;
    const found = orderItems.find((i) => String(i.id) === String(id));

    btn.classList.toggle("active", !!found);

    let badge = btn.parentElement.querySelector(".product-badge");
    if (found) {
      if (!badge) {
        badge = document.createElement("div");
        badge.className = "product-badge";
        btn.parentElement.appendChild(badge);
      }
      badge.innerText = `x${found.qty}`;
    } else if (badge) {
      badge.remove();
    }
  });
}

function addProduct(product) {
  if (!cashIsOpen) return;

  const found = orderItems.find((i) => i.id === product.id);
  if (found) {
    found.qty++;
    total += money(product.price);
  } else {
    orderItems.push({ ...product, qty: 1 });
    total += money(product.price);
  }
  renderOrder();
}

/* ================== COBRAR Y ENVIAR ================== */
function submitOrder() {
  if (!cashIsOpen) {
    alert("❌ Caja cerrada. Debes abrir caja.");
    return;
  }

  const name = (referenceNameEl?.value || "").trim();
  if (!name || orderItems.length === 0) {
    alert("Ingrese nombre y productos");
    return;
  }

  const paid = money(paidAmountEl?.value);
  const methodApi = getPaymentMethodApi();
  const isCash = methodApi === "cash";

  if (isCash && paid < total) {
    alert("El monto pagado es menor al total");
    return;
  }

  const payload = {
    reference_name: name,
    items: orderItems.map((i) => ({
      product_id: i.id,
      qty: i.qty,
      notes: ""
    })),
    payment: {
      method: methodApi,
      amount: Number(total),
      reference: ""
    }
  };

  fetch("/pos/orders", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  })
    .then(async (r) => {
      const data = await r.json().catch(() => ({}));
      if (!r.ok || !data.ok) {
        alert(data.error || "No se pudo guardar el pedido");
        throw new Error(data.error || "create_order failed");
      }
      return data;
    })
    .then(() => {
      try { successSound?.play(); } catch {}

      orderItems = [];
      total = 0;
      renderOrder();

      if (referenceNameEl) referenceNameEl.value = "";
      if (paidAmountEl) paidAmountEl.value = "";
      if (pedidoVueltoEl) pedidoVueltoEl.innerText = "0";

      cargarHistorial();
      referenceNameEl?.focus();
    })
    .catch((e) => console.error("Error submit:", e));
}

if (submitOrderBtn) {
  submitOrderBtn.addEventListener("click", (e) => {
    e.preventDefault();
    submitOrder();
  });
}

/* ================== HISTORIAL ================== */
function cargarHistorial() {
  if (!historialBody) return;

  fetch("/pos/orders/history")
    .then((res) => res.json())
    .then((data) => {
      historialBody.innerHTML = "";

      data.forEach((o) => {
        const tr = document.createElement("tr");
        tr.style.cursor = "pointer";

        // Como tu endpoint history NO trae items, muestra guion
        const productos = "—";

        let estadoBadge = "";
        const st = (o.status || "").toLowerCase();
        if (st === "cancelled") estadoBadge = `<span class="badge bg-danger">Anulado</span>`;
        else if (st === "delivered") estadoBadge = `<span class="badge bg-primary">Entregado</span>`;
        else if (st === "ready") estadoBadge = `<span class="badge bg-success">Listo</span>`;
        else estadoBadge = `<span class="badge bg-warning text-dark">En preparación</span>`;

        tr.innerHTML = `
          <td>#${o.id}</td>
          <td>${productos}</td>
          <td>${o.created_at || ""}</td>
          <td class="text-center">${estadoBadge}</td>
        `;

        tr.onclick = () => mostrarDetallePedido(o.id);
        historialBody.appendChild(tr);
      });
    })
    .catch((err) => console.error("Error historial:", err));
}

/* ================== DETALLE PEDIDO ================== */
function mostrarDetallePedido(orderId) {
  currentDetailOrderId = orderId;

  fetch(`/pos/orders/${orderId}`)
    .then(async (res) => {
      if (!res.ok) throw new Error("No se pudo cargar el pedido");
      return await res.json();
    })
    .then((o) => {
      if (detailCustomer) detailCustomer.innerText = o.reference_name || "";
      if (detailTotal) detailTotal.innerText = money(o.total);

      if (detailItems) {
        detailItems.innerHTML = "";
        (o.items || []).forEach((i) => {
          const tr = document.createElement("tr");
          tr.innerHTML = `
            <td>${i.name}</td>
            <td class="text-center">${money(i.quantity)}</td>
            <td class="text-end">$${money(i.unit_price)}</td>
            <td class="text-end">$${money(i.subtotal)}</td>
          `;
          detailItems.appendChild(tr);
        });
      }

      if (orderDetailModalEl && window.bootstrap) {
        new bootstrap.Modal(orderDetailModalEl).show();
      }
    })
    .catch((err) => console.error("Error detalle:", err));
}

/* Botones imprimir / anular */
document.addEventListener("click", (e) => {
  if (e.target && e.target.id === "btnPrintTicket") {
    window.open(`/pos/receipt/${currentDetailOrderId}`, "_blank");
  }

  if (e.target && e.target.id === "btnCancelOrder") {
    // ojo: tu backend NO tiene /cancel en el código que pegaste
    alert("⚠️ Falta endpoint /pos/orders/<id>/cancel en tu backend (no existe en pos_routes.py).");
  }
});

/* ================== CAJA UI ================== */
function setCashUI(open, infoText = "") {
  cashIsOpen = !!open;

  if (cashLockOverlay) {
    cashLockOverlay.style.display = open ? "none" : "flex";
    cashLockOverlay.classList.toggle("d-none", open);
    cashLockOverlay.classList.toggle("d-flex", !open);
  }

  if (posContent) {
    posContent.classList.toggle("opacity-50", !open);
    posContent.classList.toggle("pe-none", !open);
  }

  if (cashStatusBadge) {
    cashStatusBadge.className = open ? "badge bg-success" : "badge bg-secondary";
    cashStatusBadge.innerText = open ? "Caja: ABIERTA" : "Caja: CERRADA";
  }

  if (cashStatusInfo) cashStatusInfo.innerText = infoText || "";

  if (btnOpenCash) btnOpenCash.disabled = open;
  if (btnCloseCash) btnCloseCash.disabled = !open;

  updateChange(); // recalcula habilitación del botón cobrar
}

async function refreshCashStatus() {
  try {
    const res = await fetch("/pos/cash/status");
    const data = await res.json();

    if (data.open === true) {
      const cr = data.cash_register || {};
      const info = cr.opened_at
        ? `Desde ${cr.opened_at} | Inicial: $${money(cr.opening_amount)}`
        : "";
      setCashUI(true, info);
    } else {
      setCashUI(false, "Debes abrir caja para vender");
    }
  } catch (err) {
    console.error("Error consultando estado de caja:", err);
    setCashUI(false, "No se pudo consultar caja");
  }
}

/* ================== ABRIR CAJA ================== */
async function openCashRequest(opening_amount, notes = "") {
  const res = await fetch("/pos/cash/open", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ opening_amount, notes })
  });

  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.ok) throw new Error(data.error || "Error al abrir caja");
  return data;
}

function openCashFlow() {
  // Si existe modal, úsalo. Si no existe, fallback prompt().
  if (openCashModal && openCashForm && window.bootstrap) {
    // abrir modal real
    const modal = new bootstrap.Modal(openCashModal, { backdrop: "static", keyboard: false });
    modal.show();
    return;
  }

  // fallback sin modal
  const amountStr = prompt("Monto inicial de caja:", "0");
  if (amountStr === null) return;

  const amount = Number(amountStr);
  if (isNaN(amount) || amount < 0) {
    alert("Monto inválido");
    return;
  }

  openCashRequest(amount, "")
    .then(() => refreshCashStatus())
    .catch((e) => alert(e.message));
}

/* Overlay button */
if (btnOpenCashOverlay) btnOpenCashOverlay.addEventListener("click", openCashFlow);
/* Top bar button */
if (btnOpenCash) btnOpenCash.addEventListener("click", openCashFlow);

/* Submit modal (si existe) */
if (openCashForm) {
  openCashForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (openCashError) openCashError.classList.add("d-none");

    const amount = Number(openingAmount?.value || 0);
    const notes = (openingNotes?.value || "").trim();

    if (isNaN(amount) || amount < 0) {
      if (openCashError) {
        openCashError.innerText = "Monto inválido";
        openCashError.classList.remove("d-none");
      } else {
        alert("Monto inválido");
      }
      return;
    }

    try {
      await openCashRequest(amount, notes);

      // cerrar modal
      if (openCashModal && window.bootstrap) {
        bootstrap.Modal.getInstance(openCashModal)?.hide();
      }

      if (openingAmount) openingAmount.value = "";
      if (openingNotes) openingNotes.value = "";

      await refreshCashStatus();
    } catch (err) {
      console.error(err);
      if (openCashError) {
        openCashError.innerText = err.message || "Error de conexión";
        openCashError.classList.remove("d-none");
      } else {
        alert(err.message || "Error de conexión");
      }
    }
  });
}

/* ================== CERRAR CAJA ================== */
async function closeCashFlow() {
  if (!cashIsOpen) return;

  const amountStr = prompt("Monto de cierre (efectivo contado):", "0");
  if (amountStr === null) return;

  const closing_amount = Number(amountStr);
  if (isNaN(closing_amount) || closing_amount < 0) {
    alert("Monto inválido");
    return;
  }

  try {
    const res = await fetch("/pos/cash/close", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ closing_amount })
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) throw new Error(data.error || "Error al cerrar caja");

    await refreshCashStatus();
  } catch (e) {
    alert(e.message);
  }
}

if (btnCloseCash) btnCloseCash.addEventListener("click", closeCashFlow);

/* ================== EVENTOS ================== */
paidAmountEl?.addEventListener("input", updateChange);
paymentMethodEl?.addEventListener("change", updateChange);

/* ================== INIT ================== */
loadProducts();
cargarHistorial();
refreshCashStatus();
renderOrder();