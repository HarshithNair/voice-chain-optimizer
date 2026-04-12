// cursor.js — Logistey Custom Cursor (no external image dependencies)

document.addEventListener('DOMContentLoaded', () => {
    const dot  = document.createElement('div');
    const aura = document.createElement('div');

    dot.id  = 'cursor-dot';
    aura.id = 'cursor-aura';

    document.body.appendChild(dot);
    document.body.appendChild(aura);

    // Inject cursor styles
    const style = document.createElement('style');
    style.innerHTML = `
        html, body { cursor: none !important; }
        a, button, input, textarea, select, [role="button"] { cursor: none !important; }

        #cursor-dot {
            position: fixed;
            top: 0; left: 0;
            width: 10px; height: 10px;
            background: var(--primary, #C4622D);
            border-radius: 50%;
            pointer-events: none;
            z-index: 999999;
            transform: translate(-50%, -50%);
            transition: width 0.15s ease, height 0.15s ease, background 0.15s ease;
            mix-blend-mode: normal;
        }

        #cursor-aura {
            position: fixed;
            top: 0; left: 0;
            width: 36px; height: 36px;
            border: 1.5px solid rgba(196, 98, 45, 0.55);
            background: rgba(196, 98, 45, 0.06);
            border-radius: 4px;
            pointer-events: none;
            z-index: 999998;
            transform: translate(-50%, -50%) rotate(45deg);
            transition: width 0.25s, height 0.25s, background 0.25s, border 0.25s;
        }

        /* 3D tilt helper */
        .tilt-card {
            transition: transform 0.12s ease-out;
            transform-style: preserve-3d;
        }
    `;
    document.head.appendChild(style);

    // Cursor position & physics
    let mouseX = window.innerWidth / 2;
    let mouseY = window.innerHeight / 2;
    let auraX  = mouseX;
    let auraY  = mouseY;
    let visible = false;

    // Hide until first mouse move
    dot.style.opacity  = '0';
    aura.style.opacity = '0';

    window.addEventListener('mousemove', (e) => {
        mouseX = e.clientX;
        mouseY = e.clientY;
        dot.style.left = `${mouseX}px`;
        dot.style.top  = `${mouseY}px`;

        if (!visible) {
            visible = true;
            dot.style.opacity  = '1';
            aura.style.opacity = '1';
        }
    });

    // Animate aura with lerp spring
    (function animateAura() {
        auraX += (mouseX - auraX) * 0.14;
        auraY += (mouseY - auraY) * 0.14;
        aura.style.left = `${auraX}px`;
        aura.style.top  = `${auraY}px`;
        requestAnimationFrame(animateAura);
    })();

    // Magnetic hover effect on interactables
    const interactables = document.querySelectorAll(
        'a, button, .nav-item, .biz-card, .stat-card, .panel, .feature-card, ' +
        '.feat-icon, .step-card, .problem-card, .testi-card, .phone-mockup'
    );

    interactables.forEach(el => {
        el.addEventListener('mouseenter', () => {
            aura.style.width      = '56px';
            aura.style.height     = '56px';
            aura.style.background = 'rgba(196, 98, 45, 0.14)';
            aura.style.border     = '2px solid rgba(196, 98, 45, 0.7)';
            dot.style.width       = '6px';
            dot.style.height      = '6px';
            dot.style.background  = '#FF7722';
        });
        el.addEventListener('mouseleave', () => {
            aura.style.width      = '36px';
            aura.style.height     = '36px';
            aura.style.background = 'rgba(196, 98, 45, 0.06)';
            aura.style.border     = '1.5px solid rgba(196, 98, 45, 0.55)';
            dot.style.width       = '10px';
            dot.style.height      = '10px';
            dot.style.background  = 'var(--primary, #C4622D)';
        });
    });

    // 3D tilt on cards
    const tiltCards = document.querySelectorAll(
        '.tilt-card, .feature-card, .problem-card, .phone-mockup, .step-card, .testi-card'
    );

    tiltCards.forEach(card => {
        if (!card.classList.contains('tilt-card')) card.classList.add('tilt-card');

        card.addEventListener('mousemove', (e) => {
            const rect    = card.getBoundingClientRect();
            const x       = e.clientX - rect.left;
            const y       = e.clientY - rect.top;
            const rotateX = ((y - rect.height / 2) / (rect.height / 2)) * -8;
            const rotateY = ((x - rect.width  / 2) / (rect.width  / 2)) *  8;
            card.style.transform = `perspective(900px) rotateX(${rotateX}deg) rotateY(${rotateY}deg) scale3d(1.02,1.02,1.02)`;
        });

        card.addEventListener('mouseleave', () => {
            card.style.transform = `perspective(900px) rotateX(0deg) rotateY(0deg) scale3d(1,1,1)`;
        });
    });
});
