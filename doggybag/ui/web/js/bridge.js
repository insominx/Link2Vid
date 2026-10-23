// Calls into Python (`window.pywebview.api`). Open index.html?mock in a normal
// browser to run the UI against js/mock.js instead.
let backend = null;

function whenPywebview() {
  return new Promise((resolve) => {
    if (window.pywebview && window.pywebview.api && window.pywebview.api.ready) {
      return resolve(window.pywebview.api);
    }
    window.addEventListener("pywebviewready", () => resolve(window.pywebview.api), { once: true });
  });
}

export async function connect() {
  if (new URLSearchParams(location.search).has("mock")) {
    const { createMock } = await import("./mock.js");
    backend = createMock();
  } else {
    backend = await whenPywebview();
  }
  return backend;
}

export function call(method, ...args) {
  if (!backend || typeof backend[method] !== "function") {
    console.warn("[doggybag] backend method unavailable:", method);
    return Promise.resolve(null);
  }
  try {
    return Promise.resolve(backend[method](...args)).catch((error) => {
      console.error("[doggybag]", method, error);
      return null;
    });
  } catch (error) {
    console.error("[doggybag]", method, error);
    return Promise.resolve(null);
  }
}
