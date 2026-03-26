/* ModelDev – app.js */

// ── Auto-dismiss alerts after 4s ──────────────────────────────────────────
document.addEventListener('DOMContentLoaded', function () {
  document.querySelectorAll('.alert.alert-dismissible').forEach(function (el) {
    setTimeout(function () {
      const bsAlert = bootstrap.Alert.getOrCreateInstance(el);
      bsAlert.close();
    }, 4000);
  });
});

// ── Field form: verbose_name auto-fill from name ───────────────────────────
document.addEventListener('DOMContentLoaded', function () {
  const nameInput    = document.querySelector('input[name="name"]');
  const verboseInput = document.querySelector('input[name="verbose_name"]');
  if (nameInput && verboseInput) {
    let userEditedVerbose = verboseInput.value !== '';
    verboseInput.addEventListener('input', function () {
      userEditedVerbose = verboseInput.value !== '';
    });
    nameInput.addEventListener('input', function () {
      if (!userEditedVerbose) {
        verboseInput.value = nameInput.value
          .replace(/_/g, ' ')
          .replace(/\b\w/g, c => c.toUpperCase());
      }
    });
  }
});

// ── Clazz form: verbose_name / plural auto-fill ────────────────────────────
document.addEventListener('DOMContentLoaded', function () {
  const classNameInput  = document.querySelector('input[name="name"]');
  const verboseName     = document.querySelector('input[name="verbose_name"]');
  const verbosePlural   = document.querySelector('input[name="verbose_name_plural"]');

  if (!classNameInput || !verboseName || !verbosePlural) return;

  // Only if this is the clazz form (both fields present)
  let userEditedName   = verboseName.value !== '';
  let userEditedPlural = verbosePlural.value !== '';

  verboseName.addEventListener('input',   () => { userEditedName   = verboseName.value   !== ''; });
  verbosePlural.addEventListener('input', () => { userEditedPlural = verbosePlural.value !== ''; });

  classNameInput.addEventListener('input', function () {
    // PascalCase → "Pascal Case"
    const readable = classNameInput.value
      .replace(/([A-Z])/g, ' $1')
      .trim()
      .replace(/\s+/g, ' ');
    if (!userEditedName)   verboseName.value   = readable;
    if (!userEditedPlural) verbosePlural.value = readable + 's';
  });
});

// ── Confirm dangerous actions ──────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', function () {
  document.querySelectorAll('[data-confirm]').forEach(function (el) {
    el.addEventListener('click', function (e) {
      if (!confirm(el.dataset.confirm)) {
        e.preventDefault();
        return false;
      }
    });
  });
});

// ── Field type panel visibility (for field_form) ───────────────────────────
// (also handled inline in the template but kept here for DRYness if needed)

// ── Tooltip init ───────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', function () {
  document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(function (el) {
    new bootstrap.Tooltip(el);
  });
});
