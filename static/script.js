  // ── State ──────────────────────────────────────────────────────────────────
  let questions     = [];
  let currentIndex  = 0;
  let score         = 0;
  let wrongTopics   = [];
  let answered      = false;
  let selectedFile  = null;
  let numQuestions  = 4;
  let answerLog     = [];
  let lastUploadedFilename = null;

  // ── Screen Helper ──────────────────────────────────────────────────────────
  function showScreen(id) {
    document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
    document.getElementById(id).classList.add('active');
  }

  // ── Question Count Selector ────────────────────────────────────────────────
  function setQuestionCount(btn, count) {
    document.querySelectorAll('.qc-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    numQuestions = count;
    document.getElementById('qc-selected').textContent = count;
  }

  // ── Drag & Drop ────────────────────────────────────────────────────────────
  function handleDragOver(e) {
    e.preventDefault();
    document.getElementById('drop-zone').classList.add('dragover');
  }
  function handleDragLeave(e) {
    document.getElementById('drop-zone').classList.remove('dragover');
  }
  function handleDrop(e) {
    e.preventDefault();
    document.getElementById('drop-zone').classList.remove('dragover');
    const file = e.dataTransfer.files[0];
    if (file) processFile(file);
  }

  // ── File Select ────────────────────────────────────────────────────────────
  function handleFileSelect(e) {
    const file = e.target.files[0];
    if (file) processFile(file);
  }

  // ── Process File ───────────────────────────────────────────────────────────
  function processFile(file) {
    if (!file.name.endsWith('.pdf')) {
      showError('Only PDF files are allowed. Please select a PDF.');
      return;
    }
    if (file.size > 20 * 1024 * 1024) {
      showError('File too large. Maximum size is 20MB.');
      return;
    }
    selectedFile = file;
    lastUploadedFilename = file.name;
    hideError();

    // Show file preview
    document.getElementById('file-name').textContent  = file.name;
    document.getElementById('file-size').textContent  = formatSize(file.size);
    document.getElementById('file-preview').style.display        = 'block';
    document.getElementById('question-count-wrap').style.display = 'block';
    document.getElementById('upload-btn').style.display          = 'block';
    document.getElementById('drop-zone').style.display           = 'none';
  }

  // ── Remove File ────────────────────────────────────────────────────────────
  function removeFile() {
    selectedFile = null;
    lastUploadedFilename = null;
    document.getElementById('file-preview').style.display        = 'none';
    document.getElementById('question-count-wrap').style.display = 'none';
    document.getElementById('upload-btn').style.display          = 'none';
    document.getElementById('drop-zone').style.display           = 'block';
    document.getElementById('pdf-input').value                   = '';
    hideError();
  }

  // ── Helpers ────────────────────────────────────────────────────────────────
  function formatSize(bytes) {
    if (bytes < 1024)        return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
  }
  function showError(msg) {
    document.getElementById('error-text').textContent  = msg;
    document.getElementById('upload-error').style.display = 'block';
  }
  function hideError() {
    document.getElementById('upload-error').style.display = 'none';
  }
  function delay(ms) {
    return new Promise(res => setTimeout(res, ms));
  }

  // ── Step Animator ──────────────────────────────────────────────────────────
  function setStep(id, state) {
    const el = document.getElementById(id);
    el.classList.remove('active', 'done');
    if (state) el.classList.add(state);
  }

  // ── Upload PDF ─────────────────────────────────────────────────────────────
  async function uploadPDF() {
    if (!selectedFile) return;

    // Disable button & show progress
    const btn = document.getElementById('upload-btn');
    btn.disabled    = true;
    btn.textContent = 'Processing...';
    document.getElementById('upload-progress').style.display = 'block';
    hideError();

    // Animate steps
    setStep('step-extract',  'active');
    setStep('step-validate', '');
    setStep('step-embed',    '');
    setStep('step-generate', '');

    const formData = new FormData();
    formData.append('file',          selectedFile);
    formData.append('num_questions', numQuestions);

    try {
      await delay(500);
      setStep('step-extract',  'done');
      setStep('step-validate', 'active');

      await delay(400);
      setStep('step-validate', 'done');
      setStep('step-embed',    'active');

      await delay(300);
      setStep('step-embed',    'done');
      setStep('step-generate', 'active');

      const res  = await fetch('/api/upload', { method: 'POST', body: formData });
      const data = await res.json();

      if (!res.ok) throw new Error(data.error || 'Upload failed');

      setStep('step-generate', 'done');
      await delay(400);

      // Update navbar badge
      document.getElementById('nav-badge').textContent = selectedFile.name;

      await startQuiz();

    } catch (err) {
      // Reset progress
      ['step-extract','step-validate','step-embed','step-generate']
        .forEach(s => setStep(s, ''));
      document.getElementById('upload-progress').style.display = 'none';
      btn.disabled    = false;
      btn.textContent = 'Analyse & Generate Quiz →';
      showError(err.message || 'Something went wrong. Please try again.');
    }
  }

  // ── Start Quiz ─────────────────────────────────────────────────────────────
  async function startQuiz() {
    try {
      const res  = await fetch('/api/questions');
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Could not load questions');

      questions    = data.questions;
      currentIndex = 0;
      score        = 0;
      wrongTopics  = [];
      answerLog    = [];

      showScreen('quiz-screen');
      renderQuestion();
    } catch (err) {
      showError(err.message);
    }
  }

  // ── Render Question ────────────────────────────────────────────────────────
  function renderQuestion() {
    answered    = false;
    const q     = questions[currentIndex];
    const pct   = (currentIndex / questions.length) * 100;

    document.getElementById('progress-fill').style.width    = pct + '%';
    document.getElementById('question-counter').textContent =
      `Question ${currentIndex + 1} of ${questions.length}`;
    document.getElementById('score-live').textContent = `Score: ${score}`;
    document.getElementById('question-topic').textContent   =
      q.topic.replace(/\b\w/g, c => c.toUpperCase());
    document.getElementById('question-text').textContent    = q.question;

    // Build options
    const grid = document.getElementById('options-grid');
    grid.innerHTML = '';
    q.options.forEach(opt => {
      const btn       = document.createElement('button');
      btn.className   = 'option-btn';
      btn.textContent = opt;
      btn.onclick     = () => selectAnswer(opt[0], btn);
      grid.appendChild(btn);
    });

    // Hide explanation
    document.getElementById('explanation-panel').classList.remove('visible');
    document.getElementById('next-btn').style.display    = 'none';
    document.getElementById('explanation-body').innerHTML =
      '<div class="loading-dots"><span></span><span></span><span></span></div>';
  }

  // ── Select Answer ──────────────────────────────────────────────────────────
  async function selectAnswer(letter, clickedBtn) {
    if (answered) return;
    answered = true;

    document.querySelectorAll('.option-btn').forEach(b => b.disabled = true);
    document.getElementById('explanation-panel').classList.add('visible');

    try {
      const res  = await fetch('/api/submit', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({
          question_id: questions[currentIndex].id,
          answer:      letter
        })
      });
      const data = await res.json();

      // Highlight options
      document.querySelectorAll('.option-btn').forEach(btn => {
        if (btn.textContent[0] === data.correct_answer)
          btn.classList.add('correct');
        else if (btn === clickedBtn)
          btn.classList.add('wrong');
      });

      // Update score
      if (data.is_correct) {
        score++;
        document.getElementById('score-live').textContent = `Score: ${score}`;
      } else {
        wrongTopics.push(data.topic);
      }

      answerLog.push({
        topic:           data.topic,
        question:        questions[currentIndex].question,
        user_answer:     letter,
        correct_answer:  data.correct_answer,
        is_correct:      data.is_correct,
        explanation:     data.explanation,
      });

      // Badge
      const badge     = document.getElementById('result-badge');
      badge.textContent = data.is_correct ? '✅ Correct!' : '❌ Incorrect';
      badge.className   = 'result-badge ' +
        (data.is_correct ? 'correct-badge' : 'wrong-badge');

      document.getElementById('explanation-body').textContent = data.explanation;

      const nextBtn         = document.getElementById('next-btn');
      nextBtn.style.display = 'block';
      nextBtn.textContent   =
        currentIndex < questions.length - 1 ? 'Next Question →' : 'View Results →';

    } catch (err) {
      document.getElementById('explanation-body').textContent =
        '⚠️ Could not get explanation. Please check your connection.';
      document.getElementById('next-btn').style.display = 'block';
    }
  }

  // ── Next Question ──────────────────────────────────────────────────────────
  async function nextQuestion() {
    currentIndex++;
    if (currentIndex < questions.length) {
      renderQuestion();
    } else {
      await showResults();
    }
  }

  // ── Show Results ───────────────────────────────────────────────────────────
  async function showResults() {
    try {
      const res  = await fetch('/api/result', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({
          score,
          total:        questions.length,
          wrong_topics: wrongTopics
        })
      });
      const data = await res.json();

      showScreen('results-screen');

      // Animate ring
      const offset = 314 - (314 * data.percentage / 100);
      setTimeout(() => {
        document.getElementById('ring-fill').style.strokeDashoffset = offset;
      }, 200);

      document.getElementById('score-percent').textContent  = data.percentage + '%';
      document.getElementById('grade-title').textContent    = data.grade;
      document.getElementById('grade-message').textContent  = data.message;
      document.getElementById('score-fraction').textContent =
        `${data.score}/${data.total}`;
      document.getElementById('progress-fill').style.width  = '100%';

      // Wrong topics
      const topicsSection = document.getElementById('topics-section');
      if (data.wrong_topics.length > 0) {
        topicsSection.style.display = 'block';
        document.getElementById('topics-list').innerHTML =
          data.wrong_topics
            .map(t => `<span class="topic-tag">${t}</span>`)
            .join('');
      } else {
        topicsSection.style.display = 'none';
      }

      buildChart();

      fetch('/api/reports/save', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({
          score:          data.score,
          total:          data.total,
          percentage:     data.percentage,
          grade:          data.grade,
          message:        data.message,
          wrong_topics:   data.wrong_topics,
          answer_detail:  answerLog,
          pdf_filename:   lastUploadedFilename,
        }),
      }).catch(() => {});

    } catch (err) {
      alert('Could not load results. Please try again.');
    }
  }

  // ── Build Results Chart ────────────────────────────────────────────────────
  function buildChart() {
    const canvas = document.getElementById('resultsChart');
    const old    = Chart.getChart(canvas);
    if (old) old.destroy();

    const topicMap = {};
    questions.forEach(q => { topicMap[q.topic] = 0; });
    wrongTopics.forEach(t => { if (t in topicMap) topicMap[t]++; });

    const labels  = Object.keys(topicMap)
      .map(t => t.replace(/\b\w/g, c => c.toUpperCase()));
    const wrongs  = Object.values(topicMap);
    const correct = wrongs.map((w, i) => {
      const total = questions
        .filter(q => q.topic === Object.keys(topicMap)[i]).length;
      return total - w;
    });

    new Chart(canvas.getContext('2d'), {
      type: 'bar',
      data: {
        labels,
        datasets: [
          {
            label:           'Correct',
            data:            correct,
            backgroundColor: 'rgba(29,78,216,0.80)',
            borderRadius:    6,
            borderSkipped:   false,
          },
          {
            label:           'Incorrect',
            data:            wrongs,
            backgroundColor: 'rgba(239,68,68,0.65)',
            borderRadius:    6,
            borderSkipped:   false,
          }
        ]
      },
      options: {
        responsive:          true,
        maintainAspectRatio: false,
        plugins: {
          legend: {
            position: 'top',
            labels: {
              font:      { family: 'DM Sans', size: 12 },
              color:     '#334155',
              boxWidth:  14,
              boxHeight: 14,
            }
          }
        },
        scales: {
          x: {
            stacked: true,
            grid:    { display: false },
            ticks:   { font: { family: 'DM Sans', size: 11 }, color: '#64748b' }
          },
          y: {
            stacked:     true,
            beginAtZero: true,
            ticks: {
              stepSize: 1,
              font:     { family: 'DM Sans', size: 11 },
              color:    '#64748b'
            },
            grid: { color: 'rgba(0,0,0,.05)' }
          }
        }
      }
    });
  }

  // ── Restart (same PDF) ─────────────────────────────────────────────────────
  function restartQuiz() {
    const canvas = document.getElementById('resultsChart');
    const old    = Chart.getChart(canvas);
    if (old) old.destroy();

    document.getElementById('topics-section').style.display     = 'none';
    document.getElementById('ring-fill').style.strokeDashoffset = 314;
    currentIndex = 0;
    score        = 0;
    wrongTopics  = [];
    answerLog    = [];
    showScreen('quiz-screen');
    renderQuestion();
  }

  // ── Upload New PDF ─────────────────────────────────────────────────────────
  function uploadNewPDF() {
    selectedFile = null;
    lastUploadedFilename = null;
    const canvas = document.getElementById('resultsChart');
    const old    = Chart.getChart(canvas);
    if (old) old.destroy();

    document.getElementById('file-preview').style.display        = 'none';
    document.getElementById('question-count-wrap').style.display = 'none';
    document.getElementById('upload-btn').style.display          = 'none';
    document.getElementById('upload-btn').disabled               = false;
    document.getElementById('upload-btn').textContent            = 'Analyse & Generate Quiz →';
    document.getElementById('drop-zone').style.display           = 'block';
    document.getElementById('upload-progress').style.display     = 'none';
    document.getElementById('pdf-input').value                   = '';
    document.getElementById('topics-section').style.display      = 'none';
    document.getElementById('ring-fill').style.strokeDashoffset  = 314;
    document.getElementById('nav-badge').textContent             = 'Finance Quiz';
    hideError();
    showScreen('upload-screen');
  }