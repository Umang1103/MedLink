/* Submit the calendar-week form as soon as another week is picked. */
(function () {
  const sel = document.getElementById("week-select");
  if (sel) sel.addEventListener("change", () => sel.form.submit());
})();
