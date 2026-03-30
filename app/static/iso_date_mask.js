(function () {
  const inputs = document.querySelectorAll('[data-iso-date-mask]');
  if (!inputs.length) return;

  const MASK = '____-__-__';
  const EDITABLE_POSITIONS = [0, 1, 2, 3, 5, 6, 8, 9];

  function digitsOnly(value) {
    return String(value || '').replace(/\D/g, '').slice(0, 8);
  }

  function buildMaskedValue(digits) {
    const chars = MASK.split('');
    digits.split('').forEach((digit, index) => {
      if (index < EDITABLE_POSITIONS.length) {
        chars[EDITABLE_POSITIONS[index]] = digit;
      }
    });
    return chars.join('');
  }

  function normalizeMaskedValue(value) {
    return buildMaskedValue(digitsOnly(value));
  }

  function editableSlotIndexFromCaret(caret, direction) {
    if (direction < 0) {
      for (let index = EDITABLE_POSITIONS.length - 1; index >= 0; index -= 1) {
        if (EDITABLE_POSITIONS[index] < caret) return index;
      }
      return 0;
    }
    for (let index = 0; index < EDITABLE_POSITIONS.length; index += 1) {
      if (EDITABLE_POSITIONS[index] >= caret) return index;
    }
    return EDITABLE_POSITIONS.length - 1;
  }

  function firstEmptySlot(value) {
    for (let index = 0; index < EDITABLE_POSITIONS.length; index += 1) {
      if (value[EDITABLE_POSITIONS[index]] === '_') return index;
    }
    return EDITABLE_POSITIONS.length - 1;
  }

  function setCaretToSlot(input, slotIndex) {
    const safeSlot = Math.max(0, Math.min(slotIndex, EDITABLE_POSITIONS.length - 1));
    const caret = EDITABLE_POSITIONS[safeSlot];
    requestAnimationFrame(() => {
      input.setSelectionRange(caret, caret + 1);
    });
  }

  function updateValidity(input) {
    const digits = digitsOnly(input.value);
    if (!digits) {
      input.setCustomValidity('');
      return;
    }
    input.setCustomValidity(digits.length === 8 ? '' : 'Wprowadź pełną datę w formacie YYYY-MM-DD.');
  }

  function prepareVisibleMask(input) {
    if (!digitsOnly(input.value)) {
      input.value = '';
      updateValidity(input);
      return;
    }
    input.value = normalizeMaskedValue(input.value);
    updateValidity(input);
  }

  inputs.forEach((input) => {
    const form = input.form;

    prepareVisibleMask(input);

    input.addEventListener('focus', () => {
      if (!digitsOnly(input.value)) {
        input.value = MASK;
      } else {
        input.value = normalizeMaskedValue(input.value);
      }
      updateValidity(input);
      setCaretToSlot(input, firstEmptySlot(input.value));
    });

    input.addEventListener('click', () => {
      prepareVisibleMask(input);
      const caret = input.selectionStart ?? 0;
      const slotIndex = editableSlotIndexFromCaret(caret, 1);
      setCaretToSlot(input, slotIndex);
    });

    input.addEventListener('blur', () => {
      const digits = digitsOnly(input.value);
      input.value = digits ? buildMaskedValue(digits) : '';
      updateValidity(input);
    });

    input.addEventListener('keydown', (event) => {
      const key = event.key;
      const selectionStart = input.selectionStart ?? 0;
      let chars = normalizeMaskedValue(input.value || MASK).split('');
      const digits = digitsOnly(chars.join(''));

      if (key === 'Tab') {
        return;
      }

      if (key === 'ArrowLeft') {
        event.preventDefault();
        setCaretToSlot(input, editableSlotIndexFromCaret(selectionStart, -1));
        return;
      }

      if (key === 'ArrowRight') {
        event.preventDefault();
        setCaretToSlot(input, editableSlotIndexFromCaret(selectionStart + 1, 1));
        return;
      }

      if (key === 'Home') {
        event.preventDefault();
        setCaretToSlot(input, 0);
        return;
      }

      if (key === 'End') {
        event.preventDefault();
        setCaretToSlot(input, EDITABLE_POSITIONS.length - 1);
        return;
      }

      if (key === 'Backspace') {
        event.preventDefault();
        const slotIndex = editableSlotIndexFromCaret(selectionStart, -1);
        chars[EDITABLE_POSITIONS[slotIndex]] = '_';
        input.value = chars.join('');
        updateValidity(input);
        setCaretToSlot(input, slotIndex);
        return;
      }

      if (key === 'Delete') {
        event.preventDefault();
        const slotIndex = editableSlotIndexFromCaret(selectionStart, 1);
        chars[EDITABLE_POSITIONS[slotIndex]] = '_';
        input.value = chars.join('');
        updateValidity(input);
        setCaretToSlot(input, slotIndex);
        return;
      }

      if (/^\d$/.test(key)) {
        event.preventDefault();
        if (digits.length >= 8 && !input.value.includes('_')) {
          return;
        }
        const slotIndex = editableSlotIndexFromCaret(selectionStart, 1);
        chars[EDITABLE_POSITIONS[slotIndex]] = key;
        input.value = chars.join('');
        updateValidity(input);
        setCaretToSlot(input, Math.min(slotIndex + 1, EDITABLE_POSITIONS.length - 1));
        return;
      }

      if (key.length === 1) {
        event.preventDefault();
      }
    });

    if (form) {
      form.addEventListener('submit', () => {
        const digits = digitsOnly(input.value);
        input.value = digits ? buildMaskedValue(digits).replace(/_/g, '') : '';
        updateValidity(input);
      });
    }
  });
})();
