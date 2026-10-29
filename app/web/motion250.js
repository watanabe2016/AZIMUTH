/* 首页动效：同一属性只用一个驱动，隐藏后清理有限动画和跟随时钟。 */
(() => {
  'use strict';
  const stage = document.querySelector('#stage');
  const active = new Map(), seen = new Map(), samples = [];
  let suspended=false;
  const ease = 'cubic-bezier(.65,0,.35,1)';
  const all = selector => [...document.querySelectorAll(selector)];
  const allowed = () => !suspended && !document.hidden && !document.documentElement.classList.contains('motion-off')
    && !matchMedia('(prefers-reduced-motion: reduce)').matches;
  const mark = id => seen.set(id, (seen.get(id) || 0) + 1);
  const visible = element => element && element.getBoundingClientRect().width > 0 && getComputedStyle(element).display !== 'none';
  function animate(id, element, frames, duration = 680, delay = 0) {
    if (!visible(element) || !allowed()) return;
    const keys = Object.keys(frames[0]).filter(key => key !== 'offset');
    const slot = keys.join('|');
    let states = active.get(element);
    if (!states) { states = new Map(); active.set(element, states); }
    const overlapping = [...states.entries()].filter(([key])=>key.split('|').some(property=>keys.includes(property)));
    const previous = overlapping.length ? overlapping[0][1] : null;
    if (previous) {
      const style = getComputedStyle(element);
      frames[0] = Object.fromEntries(keys.map(key => [key, style[key]]));
      for(const [key,motion] of overlapping) {
        const remainder=key.split('|').filter(property=>!keys.includes(property));
        if(remainder.length) {
          const tail=motion.effect.getKeyframes().at(-1);
          const rest=element.animate([Object.fromEntries(remainder.map(k=>[k,style[k]])),
            Object.fromEntries(remainder.map(k=>[k,tail[k]]))],{duration:Math.max(180,duration),easing:ease,fill:'both'});
          const restKey=remainder.join('|');states.set(restKey,rest);
          rest.finished.then(()=>{if(states.get(restKey)===rest){states.delete(restKey);if(!states.size)active.delete(element);}rest.cancel();}).catch(()=>{});
        }
        motion.cancel();states.delete(key);
      }
    }
    const animation = element.animate(frames, {duration, delay, easing:ease, fill:'both'});
    states.set(slot, animation);
    mark(id);
    animation.finished.then(() => {
      if (states.get(slot) === animation) states.delete(slot);
      if (!states.size) active.delete(element);
      animation.cancel();
    }).catch(() => {});
  }
  const reveal = (id, element, delay=0) => animate(id, element,
    [{opacity:0, translate:'0px 12px'}, {opacity:1, translate:'0px 0px'}], 720, delay);
  const pulse = (id, element) => animate(id, element,
    [{scale:'1'}, {scale:'1.018', offset:.5}, {scale:'1'}], 700);
  const oldCancel = window.cancelHomeEntrance;
  let followFrame=0, scrollFrame=0, lastFrame=0, restoration=0, resizeTimer=0;
  const followers = [];
  function cancel() {
    suspended=true;
    oldCancel();
    for (const states of active.values()) for (const animation of states.values()) animation.cancel();
    active.clear();
    cancelAnimationFrame(followFrame); cancelAnimationFrame(scrollFrame);
    followFrame=scrollFrame=lastFrame=0;
    clearTimeout(resizeTimer);
    for (const item of followers) { item.x=item.y=item.tx=item.ty=0; item.element.style.translate='0px 0px'; }
    topButton.style.opacity='0';
  }
  window.cancelHomeEntrance = cancel;
  window.prepareHomeEntrance = () => {
    cancel();
    document.documentElement.classList.add('home-pending');
  };
  const topButton = document.createElement('button');
  topButton.id='motion-top'; topButton.textContent='↑'; topButton.setAttribute('aria-label','返回顶部');
  topButton.style.cssText='position:fixed;right:24px;bottom:24px;width:42px;height:42px;border-radius:12px;border:1px solid #8aabdd;background:#1666da;color:white;opacity:0;pointer-events:none;z-index:20';
  const progress = document.createElement('div'); progress.id='motion-scroll-progress';
  progress.style.cssText='position:fixed;left:0;top:0;right:0;height:2px;background:#387bea;transform-origin:left;transform:scaleX(0);pointer-events:none;z-index:21';
  const toast = document.createElement('div'); toast.id='motion-toast'; toast.setAttribute('role','status');
  toast.style.cssText='position:fixed;left:50%;bottom:30px;translate:-50% 0;border-radius:12px;padding:10px 18px;background:#18314c;color:#fff;opacity:0;pointer-events:none;z-index:22';
  const loading = document.createElement('div'); loading.id='motion-loading';
  loading.style.cssText='position:fixed;left:0;top:0;height:2px;width:28%;background:#4d97f7;pointer-events:none;z-index:23';
  document.body.append(topButton,progress,toast,loading);
  const heroArt=document.createElement('div');heroArt.id='motion-hero-art';
  heroArt.setAttribute('aria-hidden','true');
  heroArt.style.cssText='position:absolute;inset:0;display:none;pointer-events:none;z-index:0';
  document.querySelector('.masthead').prepend(heroArt);
  const scrollShade=document.createElement('div');
  scrollShade.style.cssText='position:absolute;inset:0;pointer-events:none;background:linear-gradient(transparent 45%,rgba(15,33,58,.16));opacity:0;z-index:0';
  document.querySelector('.masthead').append(scrollShade);
  all('h1 .accent').forEach(accent=>{
    const line=document.createElement('span');line.className='motion-title-line';line.setAttribute('aria-hidden','true');
    line.style.cssText='position:absolute;left:0;right:0;bottom:-5px;height:3px;background:currentColor;transform-origin:left;pointer-events:none';
    accent.style.position='relative';accent.append(line);
  });
  const css = document.createElement('style');
  css.textContent=`.visual{transition:none!important}.card{transition:box-shadow 480ms ${ease},border-color 480ms ${ease}!important}.card:hover,.card:active{transform:none!important}.start-link,.card__action{transition:background-color 420ms ${ease},border-color 420ms ${ease}!important}.start-link .arrow,.start-link:hover .arrow{transform:none!important;transition:none!important}.motion-off #motion-top,.motion-off #motion-loading{display:none}#motion-top:focus-visible{outline:3px solid #99bcff;outline-offset:3px}`;
  document.head.append(css);
  const artCSS=document.createElement('style');
  artCSS.textContent='[data-app-theme="anna"] .masthead{background:none!important}[data-app-theme="anna"] #motion-hero-art{display:block!important;background:var(--anna-home-reference) 100% 10.6%/116.64% auto no-repeat}[data-app-theme="anna"] .motion-title-line{display:none}';
  document.head.append(artCSS);
  function entrance() {
    suspended=false;
    if (!allowed()) {
      cancel();
      if (!document.hidden) document.documentElement.classList.remove('home-pending');
      return;
    }
    reveal('home-01',stage);
    reveal('home-02',document.querySelector('h1'),45);
    all('.motion-title-line').forEach(e => animate('home-03',e,[{scale:'0 1'},{scale:'1 1'}],850,150));
    all('.hero-subtitle,.intro p').forEach(e => reveal('home-04',e,130));
    all('#motion-hero-art,.chiyo-cover').forEach(e => animate('home-05',e,[{opacity:0},{opacity:1}],780,60));
    all('.anna-card-motifs i').forEach(e => reveal('home-07',e,210));
    all('.cover-note').forEach(e => reveal('home-08',e,240));
    all('.anna-home-motifs .dots,.anna-card-motifs').forEach(e=>animate('home-09',e,[{opacity:0},{opacity:1}],900));
    all('.card').forEach((e,i) => {
      reveal('home-16',e,150+i*95);
      allWithin(e,'.card__subtitle').forEach(v => reveal('home-19',v,280+i*95));
      allWithin(e,'.visual').forEach(v => reveal('home-23',v,210+i*95));
      allWithin(e,'svg path,svg line,svg circle').forEach((path,j) => {
        if (!path.getTotalLength || !visible(path)) return;
        const length=path.getTotalLength();
        animate('home-18',path,[{strokeDasharray:`${length} ${length}`,strokeDashoffset:`${length}`},
          {strokeDasharray:`${length} ${length}`,strokeDashoffset:'0'}],950,210+i*95+j*25);
      });
    });
    restoration=window.scrollY;
    if (restoration>0) animate('home-50',stage,[{opacity:.85},{opacity:1}],650);
    playPaperFlight(stage);
    requestAnimationFrame(() =>
      document.documentElement.classList.remove('home-pending'));
  }
  function allWithin(element,selector) { return [...element.querySelectorAll(selector)]; }
  window.playHomeEntrance=entrance;
  function followTick(now) {
    if (!allowed()) { cancel(); return; }
    const dt=Math.min(.06,lastFrame ? (now-lastFrame)/1000 : 1/60);
    if (lastFrame) samples.push(now-lastFrame);
    if(samples.length>1200) samples.shift();
    lastFrame=now;
    const alpha=1-Math.exp(-14*dt);
    let moving=false;
    for(const f of followers) {
      f.x+=(f.tx-f.x)*alpha; f.y+=(f.ty-f.y)*alpha;
      if(Math.abs(f.tx-f.x)+Math.abs(f.ty-f.y)>.025) moving=true;
      else { f.x=f.tx; f.y=f.ty; }
      f.element.style.translate=`${f.x.toFixed(3)}px ${f.y.toFixed(3)}px`;
    }
    followFrame=moving ? requestAnimationFrame(followTick) : 0;
    if(!moving) lastFrame=0;
  }
  function requestFollow() { if(!followFrame && allowed()) {heroArt.style.willChange='transform';followFrame=requestAnimationFrame(followTick);} }
  const masthead=document.querySelector('.masthead');
  const cover=heroArt;
  if(cover && masthead) {
    const f={element:cover,x:0,y:0,tx:0,ty:0}; followers.push(f);
    masthead.addEventListener('pointermove',e=>{
      if(!allowed() || !visible(cover)) return;
      const r=masthead.getBoundingClientRect();
      f.tx=((e.clientX-r.left)/r.width-.5)*6; f.ty=((e.clientY-r.top)/r.height-.5)*4;
      mark('home-06'); requestFollow();
    },{passive:true});
    masthead.addEventListener('pointerleave',()=>{f.tx=f.ty=0;requestFollow();});
  }
  all('.start-link,.card__action').forEach(button=>{
    const hero=button.matches('.start-link');
    button.addEventListener('pointerenter',()=>{
      animate(hero?'home-11':'home-17',button,[{scale:1},{scale:1.018}],420);
      if(hero) allWithin(button,'.arrow').forEach(arrow=>animate('home-13',arrow,[{translate:'0px 0px'},{translate:'5px 0px'}],460));
    });
    button.addEventListener('pointerleave',()=>animate(hero?'home-11':'home-17',button,[{scale:1.018},{scale:1}],420));
    button.addEventListener('pointerdown',()=>animate(hero?'home-12':'home-20',button,[{scale:1},{scale:.976}],180));
    const release=()=>animate(hero?'home-12':'home-20',button,[{scale:.976},{scale:1}],360);
    button.addEventListener('pointerup',release);button.addEventListener('pointercancel',release);
    button.addEventListener('focus',()=>animate('home-14',button,[{outlineColor:'transparent'},{outlineColor:'#78aaff'}],380));
    button.addEventListener('click',()=>{
      toast.textContent=hero?'正在打开练习…':'正在切换页面…';
      animate('home-49',toast,[{opacity:0},{opacity:1,offset:.25},{opacity:1,offset:.7},{opacity:0}],1100);
    });
  });
  all('.card').forEach(card=>{
    card.addEventListener('pointerenter',()=>animate('home-17',card,[{translate:'0px 0px'},{translate:'0px -3px'}],460));
    card.addEventListener('pointerleave',()=>animate('home-17',card,[{translate:'0px -3px'},{translate:'0px 0px'}],460));
  });
  const observer=new IntersectionObserver(entries=>{
    for(const entry of entries) if(entry.isIntersecting && !entry.target.dataset.motionSeen) {
      entry.target.dataset.motionSeen='1';
      reveal(entry.target.id==='footnote'?'home-40':'home-36',entry.target);
    }
  },{threshold:.12});
  all('.card,#footnote').forEach(e=>observer.observe(e));
  function scroll() {
    scrollFrame=0;
    const fraction=window.scrollY/Math.max(1,document.documentElement.scrollHeight-innerHeight);
    progress.style.transform=`scaleX(${Math.min(1,fraction)})`;mark('home-39');
    const show=window.scrollY>140;
    if(topButton.dataset.open!==String(show)) {
      topButton.dataset.open=String(show);topButton.style.pointerEvents=show?'auto':'none';
      topButton.style.opacity=show?'1':'0';
      animate('home-38',topButton,[{opacity:show?0:1},{opacity:show?1:0}],520);
    }
    if(allowed() && cover && visible(cover)) {
      /* 滚动只驱动缩放，指针只驱动位移，避免两种输入互相抢属性。 */
      cover.style.scale=String(1+Math.min(.014,window.scrollY*.00003));mark('home-10');
      scrollShade.style.opacity=String(Math.min(1,window.scrollY/650));mark('home-37');
    }
  }
  window.addEventListener('scroll',()=>{if(!scrollFrame)scrollFrame=requestAnimationFrame(scroll);},{passive:true});
  topButton.addEventListener('click',()=>window.scrollTo({top:0,behavior:allowed()?'smooth':'instant'}));
  const dashboard=window.updateDashboard;
  window.updateDashboard=data=>{
    const elements=all('.metric__number,#review-label,#start-label,#footnote,#review-button');
    const old=new Map(elements.map(e=>[e,e.textContent]));
    const disabled=document.querySelector('#review-button').disabled;
    const oldDone=Number(document.querySelector('#done-count').textContent)||0;
    dashboard(data);
    for(const [id,target] of [['question-count',data.questionCount],['done-count',data.doneCount],['wrong-count',data.wrongCount]]) {
      const element=document.getElementById(id);
      if(Number(target)!==(Number(old.get(element))||0))
        animate('home-22',element,[{rotate:'x -12deg',opacity:.75},{rotate:'x 0deg',opacity:1}],700);
    }
    for(const e of elements) if(old.get(e)!==e.textContent) {
      const id=e.id==='start-label'?'home-26':e.id==='review-label'?'home-24':e.id==='footnote'?'home-25':'home-22';
      reveal(id,e);
      if(e.id==='start-label') pulse('home-27',e);
    }
    all('.metric__number').forEach(e=>mark('home-21'));
    if(disabled!==document.querySelector('#review-button').disabled) {
      reveal('home-15',document.querySelector('#review-button'));pulse('home-28',document.querySelector('#review-button'));
    }
    if(Number(data.doneCount)>oldDone) {
      pulse('home-29',document.querySelector('#done-count'));
      animate('home-30',document.querySelector('#done-count'),[{color:'currentColor'},{color:'#53b49b',offset:.5},{color:'currentColor'}],850);
    }
    if(!data.hasQuestions) {reveal('home-45',document.querySelector('#footnote'));}
  };
  const theme=window.setApplicationTheme;
  window.setApplicationTheme=(...args)=>{
    cancel();theme(...args);suspended=false;
    animate('home-41',stage,[{opacity:.75},{opacity:1}],780);
    all('#motion-hero-art,.chiyo-cover').forEach(e=>animate('home-42',e,[{opacity:0},{opacity:1}],780,80));
    all('.card').forEach((e,i)=>reveal('home-43',e,120+i*90));
    all('.anna-home-motifs,.cover-note').forEach(e=>reveal('home-44',e,220));
  };
  window.addEventListener('resize',()=>{
    clearTimeout(resizeTimer);
    resizeTimer=setTimeout(()=>pulse('home-48',document.querySelector('.cards')),140);
  });
  window.addEventListener('load',()=>{
    animate('home-46',loading,[{translate:'-110% 0'},{translate:'450% 0'}],1050);
    setTimeout(()=>loading.remove(),1150);
    all('.visual').forEach(e=>reveal('home-47',e,100));
  });
  document.addEventListener('visibilitychange',()=>{
    if(document.hidden) {restoration=window.scrollY;cancel();}
    else {suspended=false;if(allowed()) {
      window.scrollTo(0,restoration);
      if(document.documentElement.classList.contains('home-pending'))
        requestAnimationFrame(()=>window.playHomeEntrance && window.playHomeEntrance());
      else reveal('home-50',stage);
    }}
  });
  new MutationObserver(()=>{if(!allowed())cancel();}).observe(document.documentElement,{attributes:true,attributeFilter:['class']});
  window.motion250Probe=()=>({seen:Object.fromEntries(seen),active:[...active.values()].reduce((n,v)=>n+v.size,0),
    following:!!followFrame,frameIntervals:samples.slice(),reduced:!allowed()});
  window.motion250Entrance=entrance;
})();
