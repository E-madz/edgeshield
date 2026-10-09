from django.contrib import admin

from .models import Domain, Origin, PurgeJob


class OriginInline(admin.TabularInline):
    model = Origin
    extra = 0


@admin.register(Domain)
class DomainAdmin(admin.ModelAdmin):
    list_display = ('name', 'owner', 'is_active', 'created_at')
    list_select_related = ('owner',)
    search_fields = ('name', 'owner__email')
    inlines = [OriginInline]


@admin.register(PurgeJob)
class PurgeJobAdmin(admin.ModelAdmin):
    list_display = ('id', 'domain', 'kind', 'status', 'attempts', 'created_at')
    list_filter = ('status', 'kind')
    list_select_related = ('domain',)
    readonly_fields = ('created_at', 'started_at', 'finished_at')
