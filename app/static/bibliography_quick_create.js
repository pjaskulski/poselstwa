(function () {
  const modal = document.getElementById('quick-bibliography-modal');
  const form = document.getElementById('quick-bibliography-form');
  if (!modal || !form) {
    return;
  }

  const errorBox = document.getElementById('quick-bibliography-errors');
  const targetSelectInput = form.elements.namedItem('target_select_id');
  const closeButtons = modal.querySelectorAll('[data-bibliography-modal-close]');

  function hideErrors() {
    errorBox.textContent = '';
    errorBox.classList.add('hidden');
  }

  function showErrors(messages) {
    errorBox.innerHTML = messages.join('<br>');
    errorBox.classList.remove('hidden');
  }

  function openModal(targetSelectId) {
    form.reset();
    hideErrors();
    targetSelectInput.value = targetSelectId;
    modal.classList.remove('hidden');
    modal.setAttribute('aria-hidden', 'false');
    const shortCitation = document.getElementById('quick-bibliography-short-citation');
    if (shortCitation) {
      shortCitation.focus();
    }
  }

  function closeModal() {
    modal.classList.add('hidden');
    modal.setAttribute('aria-hidden', 'true');
  }

  document.querySelectorAll('.quick-bibliography-trigger').forEach((button) => {
    button.addEventListener('click', () => {
      const targetSelectId = button.dataset.targetSelect;
      if (!targetSelectId) {
        return;
      }
      openModal(targetSelectId);
    });
  });

  closeButtons.forEach((button) => {
    button.addEventListener('click', closeModal);
  });

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    hideErrors();

    const targetSelectId = targetSelectInput.value;
    const targetSelect = document.getElementById(targetSelectId);
    if (!targetSelect) {
      showErrors(['Nie znaleziono pola docelowego dla nowej pozycji bibliograficznej.']);
      return;
    }

    const payload = Object.fromEntries(new FormData(form).entries());

    try {
      const response = await fetch('/bibliography/quick-create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const result = await response.json();
      if (!response.ok || !result.ok) {
        throw result;
      }
      const option = document.createElement('option');
      option.value = String(result.item.id);
      option.textContent = result.item.short_citation;
      option.dataset.title = result.item.title || result.item.note || '';
      option.dataset.year = result.item.publication_year || '';
      option.selected = true;
      targetSelect.appendChild(option);
      targetSelect.value = String(result.item.id);
      targetSelect.dispatchEvent(new Event('change', { bubbles: true }));
      closeModal();
    } catch (error) {
      const messages = Array.isArray(error?.errors) && error.errors.length ? error.errors : ['Nie udało się dodać pozycji bibliograficznej.'];
      showErrors(messages);
    }
  });
})();
