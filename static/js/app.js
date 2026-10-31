document.addEventListener('DOMContentLoaded', () => {
  const questionList = document.querySelector('#question-list');
  const addQuestionButton = document.querySelector('#add-question');
  const questionTemplate = document.querySelector('#question-template');

  if (questionList && addQuestionButton && questionTemplate) {
    const updateQuestionControls = () => {
      const rows = [...questionList.querySelectorAll('.question-editor')];
      rows.forEach((row, index) => {
        row.querySelector('legend').textContent = `Question ${index + 1}`;
        row.querySelector('.remove-question').disabled = rows.length === 1;
      });
      addQuestionButton.disabled = rows.length >= 100;
    };

    addQuestionButton.addEventListener('click', () => {
      if (questionList.querySelectorAll('.question-editor').length >= 100) return;
      questionList.append(questionTemplate.content.cloneNode(true));
      updateQuestionControls();
      questionList.lastElementChild.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    });

    questionList.addEventListener('click', (event) => {
      if (event.target.matches('.remove-question')) {
        event.target.closest('.question-editor').remove();
        updateQuestionControls();
      }
    });
    updateQuestionControls();
  }

  const uploadForm = document.querySelector('#upload-form');
  if (uploadForm) {
    const fileInput = document.querySelector('#answer_sheet');
    const previewContainer = document.querySelector('#image-preview-container');
    const previewList = document.querySelector('#answer-sheet-preview-list');
    const previewError = document.querySelector('#image-preview-error');
    let previewUrls = [];

    if (fileInput && previewContainer && previewList && previewError) {
      fileInput.addEventListener('change', () => {
        previewUrls.forEach((url) => URL.revokeObjectURL(url));
        previewUrls = [];
        previewList.replaceChildren();
        previewError.hidden = true;
        const files = Array.from(fileInput.files || []);
        previewContainer.hidden = files.length === 0;
        if (!files.length) return;

        files.forEach((file) => {
          const supported = ['image/jpeg', 'image/png'].includes(file.type) || /\.(jpe?g|png)$/i.test(file.name);
          if (!supported) {
            previewError.textContent = 'One or more files are not JPEG or PNG images.';
            previewError.hidden = false;
            return;
          }
          const card = document.createElement('div');
          card.className = 'col-md-4 scan-preview-item';
          const image = document.createElement('img');
          image.className = 'scan-preview';
          image.alt = 'Preview of ' + file.name;
          const caption = document.createElement('div');
          caption.className = 'small text-muted mt-1';
          caption.textContent = file.name;
          card.append(image, caption);
          previewList.append(card);
          const url = URL.createObjectURL(file);
          previewUrls.push(url);
          image.onerror = () => {
            previewError.textContent = 'Could not preview ' + file.name + '. Choose a valid JPEG or PNG.';
            previewError.hidden = false;
          };
          image.src = url;
        });
      });
    }

    uploadForm.addEventListener('submit', () => {
      const button = document.querySelector('#upload-submit');
      const progress = document.querySelector('#upload-progress');
      if (button) {
        button.disabled = true;
        button.textContent = 'Processing…';
      }
      if (progress) progress.hidden = false;
    });
  }
});
