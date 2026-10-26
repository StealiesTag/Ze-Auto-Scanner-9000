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
      addQuestionButton.disabled = rows.length >= 32;
    };

    addQuestionButton.addEventListener('click', () => {
      if (questionList.querySelectorAll('.question-editor').length >= 32) return;
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
