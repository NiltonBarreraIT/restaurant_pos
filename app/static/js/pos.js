let orderItems = [];
let total = 0;
let currentDetailOrderId = null;

/* ================== TOTALES ================== */
function updateTotals() {
    document.getElementById("pedidoTotal").innerText = total;
    updateChange();
    updateProductButtons();
}

function updateChange() {
    const method = paymentMethod.value;
    const paid = Number(paidAmount.value || 0);
    const hint = document.getElementById("payHint");
    const btn = submitOrder;

    let change = 0;

    if (method === "Efectivo") {
        if (paid < total) {
            hint.classList.remove("d-none");
            btn.disabled = true;
        } else {
            hint.classList.add("d-none");
            change = paid - total;
        }
    } else {
        hint.classList.add("d-none");
    }

    pedidoVuelto.innerText = change;
    btn.disabled = total === 0 || (method === "Efectivo" && paid < total);
}

/* ================== PEDIDO ================== */
function renderOrder() {
    orderList.innerHTML = "";

    orderItems.forEach((item, index) => {
        const li = document.createElement("li");
        li.className = "list-group-item d-flex justify-content-between align-items-center";

        li.innerHTML = `
            <div>
                <strong>${item.name}</strong><br>
                $${item.price} x ${item.qty} = <strong>$${item.price * item.qty}</strong>
            </div>
            <div>
                <button class="btn btn-sm btn-warning me-1">−</button>
                <button class="btn btn-sm btn-danger">❌</button>
            </div>
        `;

        li.querySelector(".btn-warning").onclick = () => {
            item.qty--;
            total -= item.price;
            if (item.qty <= 0) orderItems.splice(index, 1);
            renderOrder();
        };

        li.querySelector(".btn-danger").onclick = () => {
            total -= item.price * item.qty;
            orderItems.splice(index, 1);
            renderOrder();
        };

        orderList.appendChild(li);
    });

    updateTotals();
}

/* ================== PRODUCTOS ================== */
function loadProducts() {
    fetch("/pos/products")
        .then(r => r.json())
        .then(data => {
            products.innerHTML = "";

            data.forEach(p => {
                const col = document.createElement("div");
                col.className = "col-md-4 position-relative";

                const btn = document.createElement("button");
                btn.className = "btn btn-primary w-100 product-btn";
                btn.style.height = "120px";
                btn.style.fontSize = "22px";
                btn.innerText = `${p.name}\n$${p.price}`;
                btn.dataset.id = p.id;

                btn.onclick = () => addProduct(p);

                col.appendChild(btn);
                products.appendChild(col);
            });
        });
}

/* ================== FEEDBACK PRODUCTOS ================== */
function updateProductButtons() {
    document.querySelectorAll(".product-btn").forEach(btn => {
        const id = btn.dataset.id;
        const found = orderItems.find(i => i.id == id);

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

/* ================== AGREGAR PRODUCTO ================== */
function addProduct(product) {
    const found = orderItems.find(i => i.id === product.id);
    if (found) {
        found.qty++;
        total += product.price;
    } else {
        orderItems.push({
            id: product.id,
            name: product.name,
            price: product.price,
            qty: 1
        });
        total += product.price;
    }
    renderOrder();
}

/* ================== COBRAR Y ENVIAR (RESTAURADO) ================== */
submitOrder.onclick = () => {
    const name = referenceName.value.trim();
    const paid = Number(paidAmount.value || 0);
    const methodUI = paymentMethod.value;

    if (!name || orderItems.length === 0) {
        alert("Ingrese nombre y productos");
        return;
    }

    if (methodUI === "Efectivo" && paid < total) {
        alert("El monto pagado es menor al total");
        return;
    }

    const payload = {
        reference_name: name,
        items: orderItems.map(i => ({
            product_id: i.id,
            qty: i.qty,
            notes: ""
        })),
        payment: {
            method: methodUI === "Efectivo" ? "cash" : "transfer",
            amount: total,
            reference: ""
        }
    };

    fetch("/pos/orders", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
    })
    .then(r => r.json())
    .then(resp => {
        if (!resp.ok) {
            alert(resp.error || "No se pudo guardar el pedido");
            return;
        }

        successSound.play();

        orderItems = [];
        total = 0;
        renderOrder();

        referenceName.value = "";
        paidAmount.value = "";
        pedidoVuelto.innerText = "0";

        cargarHistorial();
        referenceName.focus();
    })
    .catch(() => alert("❌ Error de conexión"));
};

/* ================== HISTORIAL ================== */
function cargarHistorial() {
    fetch("/pos/orders/history?limit=50")
        .then(r => r.json())
        .then(data => {
            historialBody.innerHTML = "";

            data.forEach(o => {
                const tr = document.createElement("tr");
                tr.style.cursor = "pointer";

                const isCancelled =
                (o.status && o.status.toLowerCase() === "cancelled") ||
                o.cancelled === true;

                tr.innerHTML = `
                    <td>
                        ${o.created_at}
                        ${isCancelled
                            ? '<span class="badge bg-danger ms-2">ANULADO</span>'
                            : ''
                        }
                    </td>
                    <td>${o.reference_name}</td>
                    <td class="${isCancelled ? 'text-muted text-decoration-line-through' : ''}">
                        $${o.total}
                    </td>
                `;

                if (isCancelled) {
                    tr.classList.add("opacity-75");
                }

                tr.onclick = () => mostrarDetallePedido(o.id);
                historialBody.appendChild(tr);
            });
        });
}

/* ================== DETALLE PEDIDO (MODAL) ================== */
function mostrarDetallePedido(orderId) {
    currentDetailOrderId = orderId;

    fetch(`/pos/orders/${orderId}`)
        .then(r => {
            if (!r.ok) {
                throw new Error(`HTTP ${r.status}`);
            }
            return r.json();
        })
        .then(o => {
            console.log("Detalle pedido:", o);

            document.getElementById("detailCustomer").innerText =
                o.reference_name || o.customer || "-";

            document.getElementById("detailDate").innerText =
                o.created_at || "-";

            document.getElementById("detailTotal").innerText =
                o.total || 0;

            const tbody = document.getElementById("detailItems");
            tbody.innerHTML = "";

            (o.items || []).forEach(i => {
                const tr = document.createElement("tr");
                tr.innerHTML = `
                    <td>${i.name}</td>
                    <td class="text-center">${i.qty}</td>
                    <td class="text-end">$${i.price}</td>
                    <td class="text-end">$${i.subtotal}</td>
                `;
                tbody.appendChild(tr);
            });

            new bootstrap.Modal(
                document.getElementById("orderDetailModal")
            ).show();
        })
        .catch(err => {
            console.error("Error detalle pedido:", err);
            alert("No se pudo cargar el detalle del pedido");
        });
}

/* ================== BOTÓN: IMPRIMIR TICKET ================== */
document.addEventListener("click", (e) => {
    if (e.target && e.target.id === "btnPrintTicket") {
        if (!currentDetailOrderId) return;
        window.open(`/pos/receipt/${currentDetailOrderId}`, "_blank");
    }
});

/* ================== BOTÓN: ANULAR PEDIDO ================== */
document.addEventListener("click", (e) => {
    if (e.target && e.target.id === "btnCancelOrder") {
        if (!currentDetailOrderId) return;

        if (!confirm("¿Seguro que deseas anular este pedido?")) return;

        fetch(`/pos/orders/${currentDetailOrderId}/cancel`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({})
        })
        .then(r => r.json())
        .then(resp => {
            if (!resp.ok) {
                alert(resp.error || "No se pudo anular");
                return;
            }
            alert("✅ Pedido anulado");
            cargarHistorial();

            const modal = bootstrap.Modal.getInstance(
                document.getElementById("orderDetailModal")
            );
            if (modal) modal.hide();
        })
        .catch(() => alert("❌ Error de conexión al anular"));
    }
});
/* ================== COLLAPSE HISTORIAL ================== */
toggleHistory.onclick = () => {
    historyBody.classList.toggle("d-none");
};

/* ================== EVENTOS ================== */
paidAmount.addEventListener("input", updateChange);
paymentMethod.addEventListener("change", updateChange);

/* ================== INIT ================== */
loadProducts();
cargarHistorial();
