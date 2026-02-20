document.addEventListener("DOMContentLoaded", () => {
  const grid = document.getElementById("kitchenGrid");
  const emptyState = document.getElementById("emptyState");
  const connStatus = document.getElementById("connStatus");
  const lastUpdateBadge = document.getElementById("lastUpdateBadge");
  const btnRefresh = document.getElementById("btnRefresh");

  // ✅ Resumen UI
  const resumenGrid = document.getElementById("resumenGrid");
  const resumenTotal = document.getElementById("resumenTotal");
  const resumenEmpty = document.getElementById("resumenEmpty");

  function now() {
    const d = new Date();
    const pad = (n) => String(n).padStart(2, "0");
    return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  }

  function badge(estado) {
    const e = (estado || "").toUpperCase();
    if (e === "EN_PREPARACION") return `<span class="badge bg-warning text-dark">EN_PREPARACION</span>`;
    if (e === "LISTO") return `<span class="badge bg-success">LISTO</span>`;
    if (e === "ENTREGADO") return `<span class="badge bg-dark">ENTREGADO</span>`;
    if (e === "ANULADO") return `<span class="badge bg-danger">ANULADO</span>`;
    return `<span class="badge bg-secondary">${e || "-"}</span>`;
  }

  function itemsHtml(items) {
    return (items || []).map(i => `
      <div class="d-flex justify-content-between" style="font-size:0.95rem">
        <div class="text-truncate">${i.producto}</div>
        <div class="fw-bold ms-2">x${i.qty}</div>
      </div>
    `).join("");
  }

  function card(p) {
    const estado = (p.estado || "").toUpperCase();

    // Botones según estado
    let actions = "";
    if (estado === "EN_PREPARACION") {
      actions = `
        <button class="btn btn-success btn-sm w-100" data-action="LISTO" data-id="${p.id}">
          ✅ Marcar LISTO
        </button>
      `;
    } else if (estado === "LISTO") {
      actions = `
        <div class="d-grid gap-2">
          <button class="btn btn-dark btn-sm" data-action="ENTREGADO" data-id="${p.id}">
            🧾 Marcar ENTREGADO
          </button>
          <button class="btn btn-warning btn-sm" data-action="EN_PREPARACION" data-id="${p.id}">
            ↩️ Volver a PREP
          </button>
        </div>
      `;
    }

    actions += `
      <button class="btn btn-outline-danger btn-sm w-100 mt-2" data-action="ANULADO" data-id="${p.id}">
        ✖️ Anular
      </button>
    `;

    return `
      <div class="col-12 col-md-6 col-xl-4">
        <div class="card shadow-sm border-0">
          <div class="card-header bg-white d-flex justify-content-between align-items-center">
            <div class="fw-bold">#${p.numero ?? p.id} <span class="text-muted fw-normal ms-2 small">${p.hora || ""}</span></div>
            ${badge(estado)}
          </div>
          <div class="card-body">
            <div class="small text-muted">Cliente</div>
            <div class="fw-semibold mb-2">${p.cliente || "-"}</div>

            <div class="small text-muted">Items</div>
            <div class="mt-1">${itemsHtml(p.items)}</div>

            <hr class="my-3" />
            ${actions}
          </div>
        </div>
      </div>
    `;
  }

  function render(pedidos) {
    if (!pedidos || pedidos.length === 0) {
      grid.innerHTML = "";
      emptyState.classList.remove("d-none");
      return;
    }
    emptyState.classList.add("d-none");
    grid.innerHTML = pedidos.map(card).join("");
  }

  // =========================
  // ✅ Resumen producción
  // =========================
  function renderResumen(items, total) {
    if (!resumenGrid || !resumenTotal || !resumenEmpty) return;

    resumenTotal.textContent = (total ?? 0);

    resumenGrid.innerHTML = "";

    if (!items || items.length === 0) {
      resumenEmpty.classList.remove("d-none");
      return;
    }
    resumenEmpty.classList.add("d-none");

    items.forEach(item => {
      const col = document.createElement("div");
      col.className = "col-6 col-md-4 col-lg-3";

      col.innerHTML = `
        <div class="d-flex justify-content-between align-items-center border rounded p-2 bg-white resumen-item">
          <span class="text-truncate me-2">${item.producto}</span>
          <span class="badge bg-dark qty">${item.qty}</span>
        </div>
      `;

      resumenGrid.appendChild(col);
    });
  }

  async function cargarResumen() {
    try {
      // OJO: tu blueprint está montado en /cocina/, por eso usamos /cocina/api/...
      const res = await fetch("/cocina/api/resumen", { headers: { "Accept": "application/json" } });
      if (!res.ok) throw new Error("HTTP " + res.status);

      const data = await res.json();
      if (!data.ok) return;

      renderResumen(data.items || [], data.total_unidades || 0);
    } catch (e) {
      // Si falla resumen, no queremos tumbar la cocina
      console.warn("Resumen no disponible:", e);
      renderResumen([], 0);
    }
  }

  // =========================
  // Carga principal
  // =========================
  async function cargar() {
    try {
      connStatus.textContent = "Cargando...";

      // Pedidos + resumen en paralelo
      const [pedidosRes] = await Promise.all([
        fetch("/cocina/api/pedidos", { headers: { "Accept": "application/json" } }),
        cargarResumen()
      ]);

      if (!pedidosRes.ok) throw new Error("HTTP " + pedidosRes.status);

      const data = await pedidosRes.json();
      render(data.pedidos || []);

      lastUpdateBadge.textContent = "Actualizado: " + now();
      connStatus.textContent = "OK";
    } catch (e) {
      console.error(e);
      connStatus.textContent = "Error / sin conexión";
    }
  }

  async function cambiarEstado(id, estado) {
    const res = await fetch(`/cocina/api/pedidos/${id}/estado`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: JSON.stringify({ estado })
    });

    if (!res.ok) {
      const j = await res.json().catch(() => ({}));
      alert(j.error || `No se pudo cambiar estado (HTTP ${res.status})`);
      return;
    }

    // recargar lista y resumen
    await cargar();
  }

  grid.addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-action]");
    if (!btn) return;

    const id = btn.getAttribute("data-id");
    const action = btn.getAttribute("data-action");

    if (action === "ANULADO") {
      if (!confirm("¿Seguro que quieres anular este pedido?")) return;
    }

    btn.disabled = true;
    await cambiarEstado(id, action);
    btn.disabled = false;
  });

  btnRefresh?.addEventListener("click", cargar);

  cargar();
  setInterval(cargar, 5000);
});