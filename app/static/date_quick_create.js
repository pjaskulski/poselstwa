(function () {
  const modal = document.getElementById('quick-date-modal');
  const form = document.getElementById('quick-date-form');
  const errorBox = document.getElementById('quick-date-errors');
  const submitButton = document.getElementById('quick-date-submit');
  const clearButton = document.getElementById('quick-date-clear');
  const copyRangeButton = document.getElementById('quick-date-copy-range');

  if (!modal || !form || !errorBox || !submitButton || !clearButton) {
    return;
  }

  let activeConfig = null;

  function isPersistedTarget(config) {
    return config && config.entityType && Number(config.recordId) > 0;
  }

  function getInput(config) {
    return document.getElementById(config.targetInput);
  }

  function getLabel(config) {
    return document.getElementById(config.targetLabel);
  }

  function getEditButton(config) {
    return document.querySelector(
      `.quick-date-trigger[data-target-input="${config.targetInput}"][data-target-label="${config.targetLabel}"]`
    );
  }

  function syncWidget(config, date) {
    const input = getInput(config);
    const label = getLabel(config);
    const editButton = getEditButton(config);
    if (!input || !label || !editButton) {
      return;
    }

    if (date) {
      input.value = String(date.id);
      label.textContent = date.display_label;
    } else {
      input.value = '';
      label.textContent = 'brak';
    }
  }

  function openModal(config) {
    activeConfig = config;
    errorBox.classList.add('hidden');
    errorBox.textContent = '';
    form.reset();
    form.elements.entity_type.value = config.entityType || '';
    form.elements.record_id.value = config.recordId || '';
    form.elements.field_name.value = config.fieldName || '';
    form.elements.date_id.value = getInput(config)?.value || '';
    clearButton.classList.toggle('hidden', !form.elements.date_id.value);
    submitButton.textContent = 'Ustaw';
    modal.classList.remove('hidden');
    modal.setAttribute('aria-hidden', 'false');

    if (form.elements.date_id.value) {
      fetch(`/dates/${form.elements.date_id.value}/json`)
        .then((response) => response.json())
        .then((result) => {
          if (!result.ok) {
            throw new Error();
          }
          Object.entries(result.date).forEach(([key, value]) => {
            if (form.elements[key]) {
              form.elements[key].value = value ?? '';
            }
          });
          form.elements.display_label.focus();
        })
        .catch(() => {
          errorBox.textContent = 'Nie udało się pobrać danych istniejącej daty.';
          errorBox.classList.remove('hidden');
        });
    } else {
      form.elements.date_kind.value = 'exact';
      form.elements.certainty.value = 'certain';
      form.elements.display_label.focus();
    }
  }

  function closeModal() {
    modal.classList.add('hidden');
    modal.setAttribute('aria-hidden', 'true');
    activeConfig = null;
  }

  async function saveDate(payload) {
    const endpoint = isPersistedTarget(activeConfig) ? '/date-links/save' : '/dates/quick-create';
    const response = await fetch(endpoint, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Requested-With': 'fetch',
      },
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok || !result.ok) {
      throw result;
    }
    return result;
  }

  async function clearDate() {
    if (!activeConfig) {
      return;
    }
    const existingId = getInput(activeConfig)?.value;
    if (!existingId) {
      syncWidget(activeConfig, null);
      closeModal();
      return;
    }

    if (!isPersistedTarget(activeConfig)) {
      syncWidget(activeConfig, null);
      closeModal();
      return;
    }

    const response = await fetch('/date-links/clear', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Requested-With': 'fetch',
      },
      body: JSON.stringify({
        entity_type: activeConfig.entityType,
        record_id: activeConfig.recordId,
        field_name: activeConfig.fieldName,
      }),
    });
    const result = await response.json();
    if (!response.ok || !result.ok) {
      throw result;
    }
    syncWidget(activeConfig, null);
    closeModal();
  }

  document.querySelectorAll('.quick-date-trigger').forEach((button) => {
    button.addEventListener('click', () => {
      openModal({
        entityType: button.dataset.entityType || '',
        recordId: button.dataset.recordId || '',
        fieldName: button.dataset.fieldName || '',
        targetInput: button.dataset.targetInput,
        targetLabel: button.dataset.targetLabel,
      });
    });
  });

  modal.querySelectorAll('[data-modal-close]').forEach((button) => {
    button.addEventListener('click', closeModal);
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !modal.classList.contains('hidden')) {
      closeModal();
    }
  });

  clearButton.addEventListener('click', async () => {
    errorBox.classList.add('hidden');
    try {
      await clearDate();
    } catch (result) {
      errorBox.textContent = (result.errors || ['Nie udało się usunąć daty z pola.']).join(' ');
      errorBox.classList.remove('hidden');
    }
  });

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!activeConfig) {
      return;
    }

    const payload = Object.fromEntries(new FormData(form).entries());
    if (!isPersistedTarget(activeConfig)) {
      delete payload.entity_type;
      delete payload.record_id;
      delete payload.field_name;
      delete payload.date_id;
    }

    try {
      const result = await saveDate(payload);
      syncWidget(activeConfig, result.date);
      closeModal();
    } catch (result) {
      errorBox.textContent = (result.errors || ['Nie udało się zapisać daty historycznej.']).join(' ');
      errorBox.classList.remove('hidden');
    }
  });

  if (copyRangeButton) {
    copyRangeButton.addEventListener('click', () => {
      const startInput = form.elements.sort_key_start;
      const endInput = form.elements.sort_key_end;
      if (startInput && endInput && startInput.value.trim()) {
        endInput.value = startInput.value.trim();
      }
    });
  }
})();
