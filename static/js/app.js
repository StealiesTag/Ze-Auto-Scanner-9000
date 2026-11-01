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

  const automaticScanner = document.querySelector('#automatic-scanner');
  if (automaticScanner) {
    const statusUrl = automaticScanner.dataset.statusUrl;
    const setText = (id, value) => {
      const element = document.getElementById(id);
      if (element) element.textContent = value;
    };
    const renderJobs = (jobs) => {
      const body = document.getElementById('recent-captures');
      if (!body) return;
      body.replaceChildren();
      if (!jobs.length) {
        const row = document.createElement('tr');
        const cell = document.createElement('td');
        cell.colSpan = 6;
        cell.className = 'text-muted';
        cell.textContent = 'No camera captures have been queued.';
        row.append(cell);
        body.append(row);
        return;
      }
      jobs.forEach((job) => {
        const row = document.createElement('tr');
        [job.capture_id, job.captured_at ? new Date(job.captured_at).toLocaleString() : '', job.status,
          job.student_number || '—', job.score || '—'].forEach((value, index) => {
          const cell = document.createElement('td');
          if (index === 0) {
            const code = document.createElement('code');
            code.textContent = value;
            cell.append(code);
          } else cell.textContent = value;
          row.append(cell);
        });
        const action = document.createElement('td');
        if (job.review_url) {
          const link = document.createElement('a');
          link.href = job.review_url;
          link.textContent = 'Review draft';
          action.append(link);
        } else if (job.retry_url) {
          const form = document.createElement('form');
          form.method = 'post';
          form.action = job.retry_url;
          const csrf = document.querySelector('input[name="csrf_token"]');
          if (csrf) {
            const token = document.createElement('input');
            token.type = 'hidden';
            token.name = 'csrf_token';
            token.value = csrf.value;
            form.append(token);
          }
          const button = document.createElement('button');
          button.className = 'btn btn-sm btn-outline-primary';
          button.textContent = 'Retry';
          form.append(button);
          action.append(form);
        } else if (job.error) action.textContent = job.error;
        row.append(action);
        body.append(row);
      });
    };
    const pollScanner = async () => {
      try {
        const response = await fetch(statusUrl, { headers: { Accept: 'application/json' } });
        const data = await response.json();
        if (!data.ok) {
          setText('camera-error', data.error || 'Scanner status unavailable.');
          return;
        }
        setText('camera-connected', data.camera_connected ? 'Connected' : 'Disconnected');
        setText('sheet-detected', data.sheet_detected ? 'Yes' : 'No');
        setText('marker-valid', data.marker_status?.valid ? 'Yes' : 'No');
        setText('alignment-status', data.marker_status?.geometry_valid ? 'Valid' : 'Not validated');
        setText('bottom-markers', data.marker_status?.bottom_markers_visible ? 'Yes' : 'No');
        setText('page-visible', data.marker_status?.sheet_sufficiently_visible ? 'Yes' : 'No');
        setText('stability', data.stability_status ? 'Yes' : 'No');
        setText('capture-state', data.state || '—');
        setText('camera-error', data.last_error || '');
        Object.entries(data.queue || {}).forEach(([key, value]) => {
          if (key === 'capacity_limit') return;
          if (key === 'capacity') {
            setText('queue-capacity', value + ' / ' + (data.queue.capacity_limit ?? '—'));
          } else setText('queue-' + key.toLowerCase(), value);
        });
        renderJobs(data.recent_captures || []);
      } catch (_error) {
        setText('camera-connected', 'Disconnected');
        setText('camera-error', 'Could not retrieve scanner status. Check the app and database connection.');
      }
    };
    pollScanner();
    window.setInterval(pollScanner, 2000);
  }
});
