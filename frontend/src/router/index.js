import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import HomeView from '../views/HomeView.vue'
import LoginView from '../views/LoginView.vue'
import RegisterView from '../views/RegisterView.vue'
import DomainsView from '../views/DomainsView.vue'
import DomainDetailView from '../views/DomainDetailView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: HomeView, meta: { requiresAuth: true } },
    { path: '/domains', name: 'domains', component: DomainsView, meta: { requiresAuth: true } },
    {
      path: '/domains/:id',
      name: 'domain-detail',
      component: DomainDetailView,
      meta: { requiresAuth: true },
    },
    { path: '/login', name: 'login', component: LoginView },
    { path: '/register', name: 'register', component: RegisterView },
  ],
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  await auth.init()
  if (to.meta.requiresAuth && !auth.isAuthenticated) return { name: 'login' }
  if ((to.name === 'login' || to.name === 'register') && auth.isAuthenticated) return { name: 'home' }
})

export default router
