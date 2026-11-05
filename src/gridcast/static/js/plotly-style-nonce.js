const nonce = document.querySelector('meta[name="csp-nonce"]')?.content;
if (!nonce) {
  throw new Error("The page is missing its Content-Security-Policy nonce.");
}

const createElement = document.createElement.bind(document);
document.createElement = (name, options) => {
  const element = createElement(name, options);
  if (name.toLowerCase() === "style") {
    element.nonce = nonce;
  }
  return element;
};
