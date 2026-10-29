/* 首页按钮沿用原生控件：蒙版由中心展开，内容位置保持稳定。 */
(() => {
  const style = document.createElement('style');
  style.textContent = `
    .interactive-hover {position:relative;overflow:hidden;isolation:isolate;}
    .interactive-hover:hover {transform:none!important;filter:none!important;}
    .interactive-hover > .ihb-dot {display:none;}
    .interactive-hover > .ihb-original,.interactive-hover > svg {position:relative;z-index:2;}
    .interactive-hover > .ihb-incoming {position:absolute;inset:0;z-index:3;display:flex;align-items:center;justify-content:center;gap:8px;color:#fff;background:var(--blue,#1A56C4);clip-path:circle(0% at 50% 50%);pointer-events:none;transition:clip-path 480ms cubic-bezier(.22,1,.36,1);}
    .ihb-incoming svg {width:18px;height:18px;flex:none;}
    @media (hover:hover) {
      .interactive-hover:not(:disabled):hover > .ihb-incoming {clip-path:circle(75% at 50% 50%);}
    }
    .interactive-hover:not(:disabled):focus-visible > .ihb-incoming {clip-path:circle(75% at 50% 50%);}
    .interactive-hover:disabled > .ihb-incoming,html.motion-off .ihb-incoming {display:none;}
    @media (prefers-reduced-motion:reduce) {
      .ihb-incoming {display:none;}
    }`;
  document.head.append(style);
  document.querySelectorAll('.card__action,.start-link').forEach(button => {
    const original = button.querySelector('#start-label,.normal-action-label');
    if (!original) return;
    button.classList.add('interactive-hover');
    original.classList.add('ihb-original');
    const incoming=document.createElement('span');
    incoming.className='ihb-incoming';incoming.setAttribute('aria-hidden','true');
    const label=document.createElement('span');label.textContent=original.textContent;
    const icon=button.querySelector(':scope > svg');
    if (icon) incoming.append(icon.cloneNode(true));
    incoming.append(label);button.append(incoming);
    new MutationObserver(() => {label.textContent=original.textContent;})
      .observe(original,{childList:true,characterData:true,subtree:true});
  });
})();
