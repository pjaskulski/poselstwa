(function () {
  const selectionActions = document.querySelectorAll('.selection-actions');
  if (!selectionActions.length) return;

  const hideAllSelections = () => {
    selectionActions.forEach((actions) => {
      actions.classList.add('hidden');
      const link = actions.querySelector('.selection-annotate-link');
      if (link) {
        link.removeAttribute('href');
      }
    });
  };

  const buildSelectionUrl = (container, selectionText, start, end) => {
    const sourceId = container.dataset.sourceId;
    const segmentId = container.dataset.segmentId;
    const language = container.dataset.language;
    const params = new URLSearchParams({
      lang: language,
      text: selectionText,
      char_start: String(start),
      char_end: String(end),
    });
    return `/sources/${sourceId}/segments/${segmentId}/annotations/new?${params.toString()}`;
  };

  const getSelectionData = (container) => {
    const selection = window.getSelection();
    if (!selection || selection.rangeCount === 0 || selection.isCollapsed) {
      return null;
    }
    const range = selection.getRangeAt(0);
    if (!container.contains(range.commonAncestorContainer)) {
      return null;
    }
    const selectedText = selection.toString();
    if (!selectedText.trim()) {
      return null;
    }
    const prefixRange = document.createRange();
    prefixRange.selectNodeContents(container);
    prefixRange.setEnd(range.startContainer, range.startOffset);
    const start = prefixRange.toString().length;
    const end = start + selectedText.length;
    return {
      selectedText,
      start,
      end,
    };
  };

  document.addEventListener('mouseup', () => {
    const activeContainer = document.querySelector('.annotatable-text[data-selection-active="true"]');
    const data = activeContainer ? getSelectionData(activeContainer) : null;
    if (!activeContainer || !data) {
      return;
    }
    const actions = activeContainer.nextElementSibling;
    if (!actions || !actions.classList.contains('selection-actions')) {
      return;
    }
    const link = actions.querySelector('.selection-annotate-link');
    if (link) {
      link.setAttribute('href', buildSelectionUrl(activeContainer, data.selectedText, data.start, data.end));
    }
    actions.classList.remove('hidden');
  });

  document.querySelectorAll('.annotatable-text').forEach((container) => {
    container.addEventListener('mousedown', () => {
      document.querySelectorAll('.annotatable-text').forEach((item) => item.removeAttribute('data-selection-active'));
      container.setAttribute('data-selection-active', 'true');
    });
  });

  document.querySelectorAll('.selection-clear-button').forEach((button) => {
    button.addEventListener('click', () => {
      if (window.getSelection) {
        window.getSelection().removeAllRanges();
      }
      hideAllSelections();
      document.querySelectorAll('.annotatable-text').forEach((item) => item.removeAttribute('data-selection-active'));
    });
  });
})();
