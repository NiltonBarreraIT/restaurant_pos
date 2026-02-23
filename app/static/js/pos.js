/* =========================================================
   POS.JS (FULL - limpio y estable)
   - Correlativo por caja: number_in_register en historial
   - Historial: primer item + (+N)
   - Estados: prep/ready/delivered/closed/cancelled
   - Abrir/Cerrar caja: MODALES con conteo inicial/final
   - Evita redeclare / handlers duplicados
   - Imprimir ticket sin /null (dataset + currentDetailOrderId)
   - Anular pedido real: POST /pos/orders/<id>/cancel
   - ✅ REGLAS PAGO: cash/transfer requieren paid >= total
   - ✅ Vuelto: paid - total (cash/transfer)
   ========================================================= */

(() => {
  "use strict";

  /* ================== STATE ================== */
  let orderItems = [];
  let total = 0;
  let currentDetailOrderId = null;
  let cashIsOpen = false;

  /* ================== DOM SAFE GET ================== */
  const $ = (id) => document.getElementById(id);

  /* UI elements */
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

  /* Modal ABRIR */
  const openCashModal = $("openCashModal");
  const openCashForm = $("openCashForm");
  const openCashError = $("openCashError");
  const openingAmount = $("openingAmount");
  const openingNotes = $("openingNotes");

  /* Modal CERRAR */
  const closeCashModal = $("closeCashModal");
  const closeCashForm = $("closeCashForm");
  const closeCashError = $("closeCashError");
  const closingAmount = $("closingAmount");
  const closingNotes = $("closingNotes");

  /* Historial */
  const historialBody = $("historialBody");

  /* Detalle pedido (modal) */
  const orderDetailModalEl = $("orderDetailModal");
  const detailCustomer = $("detailCustomer");
  const detailDate = $("detailDate");
  const detailTotal = $("detailTotal");
  const detailItems = $("detailItems");
  const detailOrderNumber = $("detailOrderNumber");
  const detailStatusEl = $("detailStatus");

  /* Botones modal detalle */
  const btnPrintTicket = $("btnPrintTicket");
  const btnCancelOrder = $("btnCancelOrder");

  /* ================== HELPERS ================== */
  function money(n) {
    const num = Number(n || 0);
    return Number.isFinite(num) ? Math.round(num) : 0;
  }

  function getPaymentMethodApi() {
    const v = (paymentMethodEl?.value || "").trim().toLowerCase();
    if (v === "efectivo") return "cash";
    if (v === "transferencia") return "transfer";
    if (v === "cash" || v === "transfer") return v;
    return "cash";
  }

  function resetCurrentOrderUI() {
    orderItems = [];
    total = 0;
    if (referenceNameEl) referenceNameEl.value = "";
    if (paidAmountEl) paidAmountEl.value = "";
    if (pedidoVueltoEl) pedidoVueltoEl.innerText = "0";
    renderOrder();
  }

  function setModalOrderId(orderId) {
    currentDetailOrderId = orderId;
    if (btnPrintTicket) btnPrintTicket.dataset.orderId = String(orderId);
    if (btnCancelOrder) btnCancelOrder.dataset.orderId = String(orderId);
  }

  function statusBadgeHTML(st) {
    const s = String(st || "").toLowerCase();
    if (s === "cancelled") return `<span class="badge bg-danger">Anulado</span>`;
    if (s === "delivered") return `<span class="badge bg-primary">Entregado</span>`;
    if (s === "ready") return `<span class="badge bg-success">Listo</span>`;
    if (s === "closed") return `<span class="badge bg-secondary">Cerrado</span>`;
    return `<span class="badge bg-warning text-dark">En preparación</span>`;
  }

  function setModalStatus(st) {
    if (!detailStatusEl) return;
    const s = String(st || "").toLowerCase();

    if (s === "cancelled") {
      detailStatusEl.className = "order-status badge bg-danger";
      detailStatusEl.textContent = "❌ Anulado";
      return;
    }
    if (s === "delivered") {
      detailStatusEl.className = "order-status badge bg-primary";
      detailStatusEl.textContent = "✅ Entregado";
      return;
    }
    if (s === "ready") {
      detailStatusEl.className = "order-status badge bg-success";
      detailStatusEl.textContent = "🟢 Listo";
      return;
    }
    if (s === "closed") {
      detailStatusEl.className = "order-status badge bg-secondary";
      detailStatusEl.textContent = "🔒 Cerrado";
      return;
    }
    detailStatusEl.className = "order-status badge bg-warning text-dark";
    detailStatusEl.textContent = "🟡 En preparación";
  }

  function collectCounts(selector) {
    const items = [];
    document.querySelectorAll(selector).forEach((inp) => {
      const productId = parseInt(inp.dataset.productId, 10);
      const qty = parseFloat(inp.value || "0");
      if (!Number.isNaN(productId)) items.push({ product_id: productId, qty: qty });
    });
    return items;
  }

  async function postJSON(url, payload) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload || {}),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.ok === false) {
      throw new Error(data.error || data.message || `Error HTTP ${res.status}`);
    }
    return data;
  }

  /* ================== TOTALES / VALIDACIONES ================== */
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

  function updateChange() {
    if (!submitOrderBtn) return;

    const paid = money(paidAmountEl?.value);
    const methodApi = getPaymentMethodApi();
    let change = 0;

    if (total <= 0) {
      if (payHint) payHint.classList.add("d-none");
      if (pedidoVueltoEl) pedidoVueltoEl.innerText = "0";
      submitOrderBtn.disabled = true;
      return;
    }

    // cash/transfer: exige paid >= total
    if (methodApi === "cash" || methodApi === "transfer") {
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
      change = 0;
    }

    if (pedidoVueltoEl) pedidoVueltoEl.innerText = String(money(change));
    submitOrderBtn.disabled = !cashIsOpen || total <= 0 || paid < total;
  }

  function updateTotals() {
    if (pedidoTotalEl) pedidoTotalEl.innerText = String(money(total));
    updateChange();
    updateProductButtons();
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
          $${money(item.price)} x ${money(item.qty)} =
          <strong>$${money(item.price) * money(item.qty)}</strong>
        </div>
        <div>
          <button class="btn btn-sm btn-warning me-1" type="button" data-action="minus">−</button>
          <button class="btn btn-sm btn-danger" type="button" data-action="remove">❌</button>
        </div>
      `;

      li.querySelector('[data-action="minus"]').onclick = () => {
        item.qty--;
        total -= money(item.price);
        if (item.qty <= 0) orderItems.splice(index, 1);
        renderOrder();
      };

      li.querySelector('[data-action="remove"]').onclick = () => {
        total -= money(item.price) * money(item.qty);
        orderItems.splice(index, 1);
        renderOrder();
      };

      orderListEl.appendChild(li);
    });

    updateTotals();
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

  /* ================== PRODUCTOS ================== */
  function loadProducts() {
    if (!productsEl) return;

    fetch("/pos/products")
      .then((r) => r.json())
      .then((data) => {
        productsEl.innerHTML = "";

        (data || []).forEach((p) => {
          const col = document.createElement("div");
          col.className = "col-md-4 position-relative";

          const btn = document.createElement("button");
          btn.type = "button";
          btn.className = "btn btn-primary w-100 product-btn";
          btn.style.height = "120px";
          btn.style.fontSize = "22px";
          btn.innerText = `${p.name}\n$${money(p.price)}`;
          btn.dataset.id = p.id;

          btn.onclick = () => addProduct({ id: p.id, name: p.name, price: money(p.price) });

          col.appendChild(btn);
          productsEl.appendChild(col);
        });

        updateProductButtons();
      })
      .catch((e) => console.error("Error productos:", e));
  }

  /* ================== COBRAR Y ENVIAR ================== */
  function submitOrder() {
    if (!cashIsOpen) return alert("❌ Caja cerrada. Debes abrir caja.");

    const name = (referenceNameEl?.value || "").trim();
    if (!name || orderItems.length === 0) return alert("Ingrese nombre y productos");

    const paid = money(paidAmountEl?.value);
    if (paid < total) return alert("El monto pagado es menor al total");

    const payload = {
      reference_name: name,
      items: orderItems.map((i) => ({ product_id: i.id, qty: i.qty, notes: "" })),
      payment: {
        method: getPaymentMethodApi(),
        amount: Number(total),
        reference: "",
      },
    };

    fetch("/pos/orders", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })
      .then(async (r) => {
        const data = await r.json().catch(() => ({}));
        if (!r.ok || !data.ok) throw new Error(data.error || "No se pudo guardar el pedido");
        return data;
      })
      .then(() => {
        try { successSound?.play(); } catch {}
        resetCurrentOrderUI();
        cargarHistorial();
        referenceNameEl?.focus();
      })
      .catch((e) => alert(e.message || "Error al cobrar"));
  }

  submitOrderBtn?.addEventListener("click", (e) => {
    e.preventDefault();
    submitOrder();
  });

  /* ================== HISTORIAL ================== */
  function cargarHistorial() {
    if (!historialBody) return;

    fetch("/pos/orders/history")
      .then((res) => res.json())
      .then((data) => {
        historialBody.innerHTML = "";

        (data || []).forEach((o) => {
          const tr = document.createElement("tr");
          tr.style.cursor = "pointer";

          const num = o.number_in_register ?? o.order_number ?? o.id;

          const items = Array.isArray(o.items) ? o.items : [];
          let productos = "—";
          if (items.length > 0) {
            productos = items[0].name || "—";
            if (items.length > 1) productos += ` (+${items.length - 1})`;
          }

          tr.innerHTML = `
            <td>#${num}</td>
            <td>${productos}</td>
            <td>${o.created_at || ""}</td>
            <td class="text-center">${statusBadgeHTML(o.status)}</td>
          `;

          tr.onclick = () => mostrarDetallePedido(o.id);
          historialBody.appendChild(tr);
        });
      })
      .catch((err) => console.error("Error historial:", err));
  }

  /* ================== DETALLE PEDIDO ================== */
  function mostrarDetallePedido(orderId) {
    setModalOrderId(orderId);

    fetch(`/pos/orders/${orderId}`)
      .then(async (res) => {
        if (!res.ok) throw new Error("No se pudo cargar el pedido");
        return await res.json();
      })
      .then((o) => {
        if (detailCustomer) detailCustomer.innerText = o.reference_name || "";
        if (detailDate) detailDate.innerText = o.created_at || "";
        if (detailTotal) detailTotal.innerText = String(money(o.total));

        if (detailOrderNumber) {
          const num = o.number_in_register ?? o.order_number ?? "";
          detailOrderNumber.innerText = num ? `#${num}` : "";
        }

        setModalStatus(o.status);

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

  /* ================== IMPRIMIR / ANULAR ================== */
  document.addEventListener("click", async (e) => {
    const printBtn = e.target.closest("#btnPrintTicket");
    if (printBtn) {
      const id = printBtn.dataset.orderId || currentDetailOrderId;
      if (!id) return alert("No se encontró el ID del pedido para imprimir.");
      window.open(`/pos/receipt/${id}`, "_blank");
      return;
    }

    const cancelBtn = e.target.closest("#btnCancelOrder");
    if (cancelBtn) {
      const id = cancelBtn.dataset.orderId || currentDetailOrderId;
      if (!id) return alert("No se encontró el ID del pedido para anular.");

      const ok = confirm("¿Seguro que quieres ANULAR este pedido?");
      if (!ok) return;

      const reason = prompt("Motivo (opcional):", "") || "";

      try {
        const res = await fetch(`/pos/orders/${id}/cancel`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reason }),
        });

        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) throw new Error(data.error || "No se pudo anular");

        setModalStatus("cancelled");
        cargarHistorial();
      } catch (err) {
        alert(err.message || "Error al anular");
      }
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

    updateChange();
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

  /* ================== ABRIR CAJA (MODAL) ================== */
  function openCashFlow() {
    if (openCashModal && window.bootstrap) {
      new bootstrap.Modal(openCashModal, { backdrop: "static", keyboard: false }).show();
      return;
    }
    alert("Falta el modal #openCashModal en pos.html");
  }

  btnOpenCashOverlay?.addEventListener("click", openCashFlow);
  btnOpenCash?.addEventListener("click", openCashFlow);

  openCashForm?.addEventListener("submit", async (e) => {
    e.preventDefault();

    openCashError?.classList.add("d-none");
    if (openCashError) openCashError.textContent = "";

    const opening_amount = Number(openingAmount?.value || 0);
    const notes = (openingNotes?.value || "").trim();

    if (!Number.isFinite(opening_amount) || opening_amount < 0) {
      if (openCashError) {
        openCashError.textContent = "Monto inválido";
        openCashError.classList.remove("d-none");
      } else {
        alert("Monto inválido");
      }
      return;
    }

    const opening_counts = collectCounts(".open-count");

    try {
      // compat: backend puede recibir opening_counts o counts_open
      await postJSON("/pos/cash/open", {
        opening_amount,
        notes,
        opening_counts,
        counts_open: opening_counts,
      });

      bootstrap.Modal.getInstance(openCashModal)?.hide();

      if (openingAmount) openingAmount.value = "0";
      if (openingNotes) openingNotes.value = "";

      await refreshCashStatus();
      resetCurrentOrderUI();
      cargarHistorial();
    } catch (err) {
      console.error(err);
      if (openCashError) {
        openCashError.textContent = err.message || "Error al abrir caja";
        openCashError.classList.remove("d-none");
      } else {
        alert(err.message || "Error al abrir caja");
      }
    }
  });

  /* ================== CERRAR CAJA (MODAL) ================== */
  btnCloseCash?.addEventListener("click", () => {
    if (!cashIsOpen) return;
    if (closeCashModal && window.bootstrap) {
      new bootstrap.Modal(closeCashModal, { backdrop: "static", keyboard: false }).show();
      return;
    }
    alert("Falta el modal #closeCashModal en pos.html");
  });

  closeCashForm?.addEventListener("submit", async (e) => {
    e.preventDefault();

    closeCashError?.classList.add("d-none");
    if (closeCashError) closeCashError.textContent = "";

    const closing_amount = Number(closingAmount?.value || 0);
    const notes = (closingNotes?.value || "").trim();

    if (!Number.isFinite(closing_amount) || closing_amount < 0) {
      if (closeCashError) {
        closeCashError.textContent = "Monto inválido";
        closeCashError.classList.remove("d-none");
      } else {
        alert("Monto inválido");
      }
      return;
    }

    const closing_counts = collectCounts(".close-count");

    try {
      // compat: backend puede recibir closing_counts o counts_close
      await postJSON("/pos/cash/close", {
        closing_amount,
        notes,
        closing_counts,
        counts_close: closing_counts,
      });

      bootstrap.Modal.getInstance(closeCashModal)?.hide();

      if (closingAmount) closingAmount.value = "0";
      if (closingNotes) closingNotes.value = "";

      resetCurrentOrderUI();
      await refreshCashStatus();
      cargarHistorial();
    } catch (err) {
      console.error(err);
      if (closeCashError) {
        closeCashError.textContent = err.message || "Error al cerrar caja";
        closeCashError.classList.remove("d-none");
      } else {
        alert(err.message || "Error al cerrar caja");
      }
    }
  });

  /* ================== EVENTOS ================== */
  paidAmountEl?.addEventListener("input", updateChange);
  paymentMethodEl?.addEventListener("change", updateChange);

  /* ================== INIT ================== */
  loadProducts();
  cargarHistorial();
  refreshCashStatus();
  renderOrder();

  // Exponer por si el HTML los llama
  window.posRefreshHistory = cargarHistorial;
  window.mostrarDetallePedido = mostrarDetallePedido;
})();