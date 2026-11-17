// Site-wide behaviour.

// Confirm before destructive actions.
document.addEventListener("submit", function (event) {
  var message = event.target.getAttribute && event.target.getAttribute("data-confirm");
  if (message && !window.confirm(message)) {
    event.preventDefault();
  }
});

// "Print or save as PDF" button on report pages.
document.addEventListener("click", function (event) {
  if (event.target.closest && event.target.closest("[data-print]")) {
    window.print();
  }
});
