// Navigation Component
const Navigation = {
  init() {
    this.render();
    this.attachEventListeners();
  },

  render() {
    const nav = document.querySelector('.command-nav');
    if (!nav) return;

    const navItems = [
      { href: '/control-room', label: 'CONTROL ROOM', active: true },
      { href: '/tasks/ready', label: 'TASKS', active: false },
      { href: '/review', label: 'APPROVALS', active: false },
      { href: '#dependencies', label: 'DEPENDENCIES', active: false },
      { href: '/metrics', label: 'METRICS', active: false }
    ];

    nav.innerHTML = navItems.map(item => `
      <a href="${item.href}" class="${item.active ? 'active' : ''}">${item.label}</a>
    `).join('');
  },

  attachEventListeners() {
    const navLinks = document.querySelectorAll('.command-nav a');
    navLinks.forEach(link => {
      link.addEventListener('click', (e) => {
        e.preventDefault();
        this.handleNavigation(link);
      });
    });
  },

  handleNavigation(link) {
    // Remove active class from all links
    document.querySelectorAll('.command-nav a').forEach(l => l.classList.remove('active'));
    // Add active class to clicked link
    link.classList.add('active');
    
    // Handle hash links
    if (link.getAttribute('href').startsWith('#')) {
      const targetId = link.getAttribute('href').substring(1);
      const targetElement = document.getElementById(targetId);
      if (targetElement) {
        targetElement.scrollIntoView({ behavior: 'smooth' });
      }
    }
  }
};

// Export for use in other modules
window.Navigation = Navigation;
