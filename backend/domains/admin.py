from django.contrib import admin

from .models import Domain, Origin


class OriginInline(admin.TabularInline):
    model = Origin
    extra = 0


@admin.register(Domain)
class DomainAdmin(admin.ModelAdmin):
    list_display = ('name', 'owner', 'is_active', 'created_at')
    list_select_related = ('owner',)
    search_fields = ('name', 'owner__email')
    inlines = [OriginInline]
