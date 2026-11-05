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
  addQuestionButton.disabled = rows.length >= 50;
    };

    addQuestionButton.addEventListener('click', () => {
    if (questionList.querySelectorAll('.question-editor').length >= 50) return;
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
    const printStepUrl = automaticScanner.dataset.printStepUrl;
    const captureStepUrl = automaticScanner.dataset.captureStepUrl;
    let printCycleRunning = false;
    const silentPrintCheckbox = document.querySelector('input[name="silent_print_configured"]');
    if (silentPrintCheckbox && window.localStorage.getItem('paparazziSilentPrintConfigured') === 'yes') {
      silentPrintCheckbox.checked = true;
    }
    if (silentPrintCheckbox) {
      silentPrintCheckbox.addEventListener('change', () => {
        if (silentPrintCheckbox.checked) window.localStorage.setItem('paparazziSilentPrintConfigured', 'yes');
        else window.localStorage.removeItem('paparazziSilentPrintConfigured');
      });
    }
    const setText = (id, value) => {
      const element = document.getElementById(id);
      if (element) element.textContent = value;
    };
    const waitForPrintInitiation = (step) => new Promise((resolve, reject) => {
      const timeout = window.setTimeout(() => {
        window.removeEventListener('message', onMessage);
        reject(new Error('The browser did not start the print job. Check the printer and silent-print setup, then resume scanning.'));
      }, 30000);
      const onMessage = (event) => {
        if (event.origin !== window.location.origin || event.data?.type !== 'paparazzi-print-started' || event.data.step !== step) return;
        window.clearTimeout(timeout);
        window.removeEventListener('message', onMessage);
        resolve();
      };
      window.addEventListener('message', onMessage);
      const printUrl = new URL(printStepUrl, window.location.href);
      printUrl.searchParams.set('step', step);
      printUrl.searchParams.set('cache', Date.now());
      document.getElementById('printer-feed-job').src = printUrl.toString();
    });

    const runOnePagePrintCycle = async () => {
      if (printCycleRunning) return;
      printCycleRunning = true;
      try {
        while (true) {
          const statusResponse = await fetch(statusUrl, { headers: { Accept: 'application/json' } });
          const status = await statusResponse.json();
          if (!status.ok || !status.active || status.paused) break;
          if (Number(status.queue?.capacity) >= Number(status.queue?.capacity_limit)) {
            setText('camera-error', 'The capture queue is full. Scanning paused before printing another page; resume after queued scans finish.');
            const csrf = document.querySelector('input[name="csrf_token"]')?.value || '';
            await fetch('/scanning/automatic/pause', {
              method: 'POST',
              headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
              body: new URLSearchParams({ csrf_token: csrf }),
            });
            break;
          }
          const retryCurrentPage = Boolean(status.marker_status?.retry_current_page);
          const step = retryCurrentPage
            ? Number(status.marker_status.retry_step ?? status.printer_step)
            : Number(status.printer_step || 0) + 1;
          if (retryCurrentPage) {
            setText('capture-state', 'RECAPTURING LAST SHEET FOR MUSE ALIGNMENT');
          } else {
            await waitForPrintInitiation(step);
            setText('capture-state', 'WAITING 6s AFTER PRINT START');
            await new Promise((resolve) => window.setTimeout(resolve, 6000));
          }
          const csrf = document.querySelector('input[name="csrf_token"]')?.value || '';
          const body = new URLSearchParams({ step: String(step), csrf_token: csrf });
          const response = await fetch(captureStepUrl, {
            method: 'POST',
            headers: { 'Content-Type': 'application/x-www-form-urlencoded', Accept: 'application/json' },
            body,
          });
          const result = await response.json();
          if (!response.ok || !result.ok) {
            setText('camera-error', result.error || 'The capture step failed. Scanning has paused.');
            break;
          }
          if (result.done) {
            setText('capture-state', 'AUTO_STOPPED_DUPLICATE_PAGE');
            setText('camera-error', result.message || 'The same page was captured twice. Scanning stopped.');
            break;
          }
          if (result.alignment_pending) setText('capture-state', 'QUEUED_FOR_MUSE_ALIGNMENT');
        }
      } catch (error) {
        setText('camera-error', error.message || 'The printer step failed. Scanning has paused.');
        const csrf = document.querySelector('input[name="csrf_token"]')?.value || '';
        await fetch('/scanning/automatic/pause', {
          method: 'POST',
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
          body: new URLSearchParams({ csrf_token: csrf }),
        }).catch(() => {});
      } finally {
        printCycleRunning = false;
      }
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
        if (job.image_url) {
          const imageLink = document.createElement('a');
          imageLink.href = job.image_url;
          imageLink.target = '_blank';
          imageLink.rel = 'noopener';
          imageLink.textContent = 'View image';
          action.append(imageLink);
          if (job.detection?.detected_markers != null) {
            const detection = document.createElement('span');
            detection.className = 'small text-muted';
            detection.textContent = ' · ' + job.detection.detected_markers + ' markers';
            action.append(detection);
          }
          action.append(document.createElement('br'));
        }
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
        if (data.active && !data.paused) {
          if (window.localStorage.getItem('paparazziSilentPrintConfigured') === 'yes') runOnePagePrintCycle();
          else setText('camera-error', 'Configure and confirm silent printing below before the next printer step can run.');
        }
        const session = data.session || {};
        setText('session-state', session.state || 'READY');
        setText('session-captured', session.valid_sheets_captured ?? 0);
        setText('session-awaiting', session.images_awaiting_processing ?? 0);
        setText('session-graded', session.successfully_graded ?? 0);
        setText('session-review', session.review_required ?? 0);
        setText('session-rejected', session.rejected_images ?? 0);
        setText('session-duplicates', session.duplicate_captures ?? 0);
        setText('session-last-capture', session.last_successful_capture_at ? new Date(session.last_successful_capture_at).toLocaleString() : '—');
        setText('camera-error', data.last_error || '');
        const ingestion = data.ingestion || {};
        setText('ingest-received', ingestion.received ?? 0);
        setText('ingest-rejected', ingestion.rejected ?? 0);
        setText('ingest-accepted', ingestion.accepted ?? 0);
        setText('ingest-duplicates', ingestion.duplicates ?? 0);
        setText('ingest-last-received', ingestion.last_received_at ? new Date(ingestion.last_received_at).toLocaleString() : '—');
        const poller = data.camera_poller || {};
        setText('poller-camera-id', poller.camera_id || '—');
        setText('poller-running', poller.running ? (poller.enabled ? 'Polling' : 'Idle (polling off)') : 'Stopped');
        setText('poller-connection', poller.connection_status || 'stopped');
        setText('poller-last-fetch', poller.last_fetch_at ? new Date(poller.last_fetch_at).toLocaleString() : '—');
        setText('poller-last-error', poller.last_error || '—');
        setText('poller-frames', poller.frames_fetched ?? 0);
        setText('poller-accepted', poller.accepted ?? 0);
        setText('poller-rejected', poller.rejected ?? 0);
        setText('poller-duplicates', poller.duplicates ?? 0);
        setText('poller-deferred', poller.deferred ?? 0);
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
