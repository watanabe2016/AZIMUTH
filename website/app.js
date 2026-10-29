(() => {
  // 数字由服务端日志汇总，浏览器不自行累加，读取失败也不伪装成零。
  const statisticsStatus = document.querySelector('[data-stats-status]');
  let statisticsPending = false;
  async function refreshStatistics() {
    if (!statisticsStatus || statisticsPending) return;
    statisticsPending = true;
    try {
      const response = await fetch('/statistics/stats.json?t=' + Date.now(), { cache: 'no-store', signal: AbortSignal.timeout(12000) });
      if (!response.ok) throw new Error('统计暂时不可用');
      const stats = await response.json();
      if (!Number.isSafeInteger(stats.visits) || stats.visits < 0 || !Number.isSafeInteger(stats.downloads) || stats.downloads < 0) throw new Error('统计数据无效');
      document.querySelector('[data-visit-count]').textContent = stats.visits.toLocaleString('zh-CN');
      document.querySelector('[data-download-count]').textContent = stats.downloads.toLocaleString('zh-CN');
      const updated = new Date(stats.updatedAt);
      if (!Number.isFinite(updated.getTime())) throw new Error('统计时间无效');
      statisticsStatus.textContent = Date.now() - updated.getTime() > 300000 ? '统计更新延迟，显示上次记录' : '已接入真实统计 · 每分钟更新';
      statisticsStatus.title = '最近汇总：' + updated.toLocaleString('zh-CN');
    } catch (_) {
      statisticsStatus.textContent = '统计暂时不可用，稍后自动重试';
    } finally {
      statisticsPending = false;
    }
  }
  refreshStatistics();
  if (statisticsStatus) {
    setInterval(() => { if (!document.hidden) refreshStatistics(); }, 60000);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshStatistics(); });
  }

  const themeButton = document.querySelector('#theme-toggle');
  const themeLabel = document.querySelector('#theme-label');
  const themeIcon = document.querySelector('.theme-icon');
  const themeModes = ['system', 'light', 'dark'];
  const themeNames = { system: '跟随系统', light: '浅色', dark: '深色' };
  const themeIcons = { system: '◐', light: '☀', dark: '☾' };
  const themeMeta = document.querySelector('meta[name="theme-color"]');

  function savedTheme() {
    try {
      const saved = localStorage.getItem('azimuth-site-theme');
      return themeModes.includes(saved) ? saved : 'system';
    } catch (_) {
      return 'system';
    }
  }

  function applyTheme(mode, save = false) {
    if (mode === 'system') document.documentElement.removeAttribute('data-theme');
    else document.documentElement.dataset.theme = mode;
    if (themeLabel) themeLabel.textContent = themeNames[mode];
    if (themeIcon) themeIcon.textContent = themeIcons[mode];
    if (themeButton) {
      themeButton.setAttribute('aria-label', `外观：${themeNames[mode]}。点击切换`);
      themeButton.setAttribute('title', `当前外观：${themeNames[mode]}，点击切换`);
    }
    if (themeMeta) {
      const dark = mode === 'dark' || (mode === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches);
      themeMeta.content = dark ? '#111a2a' : '#f3f7ff';
    }
    if (save) {
      try {
        if (mode === 'system') localStorage.removeItem('azimuth-site-theme');
        else localStorage.setItem('azimuth-site-theme', mode);
      } catch (_) {}
    }
  }

  let currentTheme = savedTheme();
  applyTheme(currentTheme);
  themeButton?.addEventListener('click', () => {
    currentTheme = themeModes[(themeModes.indexOf(currentTheme) + 1) % themeModes.length];
    applyTheme(currentTheme, true);
  });

  const systemTheme = window.matchMedia('(prefers-color-scheme: dark)');
  systemTheme.addEventListener?.('change', () => {
    if (currentTheme === 'system') applyTheme('system');
  });
  window.addEventListener('storage', (event) => {
    if (event.key === 'azimuth-site-theme') {
      currentTheme = savedTheme();
      applyTheme(currentTheme);
    }
  });

  const dialog = document.querySelector('#donation-dialog');
  document.querySelectorAll('[data-open-donation]').forEach((button) => {
    button.addEventListener('click', () => {
      if (dialog?.showModal) dialog.showModal();
    });
  });
  document.querySelectorAll('[data-close-donation]').forEach((button) => {
    button.addEventListener('click', () => dialog?.close());
  });
  dialog?.addEventListener('click', (event) => {
    if (event.target === dialog) {
      const bounds = dialog.getBoundingClientRect();
      const inside = event.clientX >= bounds.left && event.clientX <= bounds.right && event.clientY >= bounds.top && event.clientY <= bounds.bottom;
      if (!inside) dialog.close();
    }
  });

  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const lazyVideos = [...document.querySelectorAll('[data-lazy-video]')].filter((video) => video instanceof HTMLVideoElement);
  const visibleVideos = new Set();
  const configureVideoStart = (video) => {
    const startAt = Number(video.dataset.startAt);
    if (!Number.isFinite(startAt) || startAt <= 0 || video.dataset.startAtConfigured === 'true') return;
    video.dataset.startAtConfigured = 'true';
    video.dataset.startAtReady = 'false';
    video.loop = false;
    const seekToStart = (resumeAfterSeek) => {
      const lastSafeTime = Number.isFinite(video.duration) ? Math.max(0, video.duration - 0.05) : startAt;
      const targetTime = Math.min(startAt, lastSafeTime);
      video.addEventListener('seeked', () => {
        video.dataset.startAtReady = 'true';
        if (resumeAfterSeek && visibleVideos.has(video) && !reducedMotion.matches) video.play().catch(() => {});
      }, { once: true });
      video.currentTime = targetTime;
    };
    video.addEventListener('loadedmetadata', () => seekToStart(true), { once: true });
    video.addEventListener('ended', () => seekToStart(true));
  };
  const loadVideo = (video) => {
    if (video.src || !video.dataset.src) return;
    configureVideoStart(video);
    video.src = video.dataset.src;
    video.load();
  };
  const syncVideoMotion = () => {
    lazyVideos.forEach((video) => {
      if (visibleVideos.has(video) && !reducedMotion.matches) {
        loadVideo(video);
        if (Number(video.dataset.startAt) > 0 && video.dataset.startAtReady !== 'true') return;
        video.play().catch(() => {});
      } else {
        video.pause();
      }
    });
  };
  if ('IntersectionObserver' in window) {
    const videoObserver = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          visibleVideos.add(entry.target);
          loadVideo(entry.target);
        } else {
          visibleVideos.delete(entry.target);
        }
      });
      syncVideoMotion();
    }, { threshold: 0.25 });
    lazyVideos.forEach((video) => videoObserver.observe(video));
  } else {
    lazyVideos.forEach((video) => {
      visibleVideos.add(video);
      loadVideo(video);
    });
    syncVideoMotion();
  }
  reducedMotion.addEventListener?.('change', syncVideoMotion);
  const factsButton = document.querySelector('[data-toggle-facts]');
  const factColumns = document.querySelector('.fact-columns');
  if (factsButton && factColumns) {
    const factsLabel = factsButton.querySelector('span');
    if (reducedMotion.matches) {
      factColumns.dataset.paused = 'true';
      factsButton.setAttribute('aria-pressed', 'true');
      factsButton.disabled = true;
      factsButton.firstChild.textContent = '✓ ';
      if (factsLabel) factsLabel.textContent = '系统已减少动态效果';
    } else {
      factsButton.addEventListener('click', () => {
        const paused = factColumns.dataset.paused !== 'true';
        factColumns.dataset.paused = String(paused);
        factsButton.setAttribute('aria-pressed', String(paused));
        factsButton.firstChild.textContent = paused ? '▶ ' : 'Ⅱ ';
        if (factsLabel) factsLabel.textContent = paused ? '继续动态卡片' : '暂停动态卡片';
      });
    }
  }

  const finePointer = window.matchMedia('(pointer: fine)');
  if (!reducedMotion.matches && finePointer.matches) {
    const spotlightSelector = [
      '[data-spotlight]', '.feature-card', '.fact-card', '.screens-card', '.download-panel',
      '.preview-window', '.float-note', '.download-counter', '.screenshot-frame', '.timer-demo',
      '.market-link-card', '.skin-card', '.skin-market-note', '.skin-market-close', '.skin-hero-image'
    ].join(',');
    document.addEventListener('pointermove', (event) => {
      const target = event.target instanceof Element ? event.target.closest(spotlightSelector) : null;
      if (!target) return;
      const bounds = target.getBoundingClientRect();
      target.style.setProperty('--spot-x', `${event.clientX - bounds.left}px`);
      target.style.setProperty('--spot-y', `${event.clientY - bounds.top}px`);
    }, { passive: true });
  }
})();
