from django.contrib import admin
from .models import RehostedMedia

@admin.register(RehostedMedia)
class RehostedMediaAdmin(admin.ModelAdmin):
    """
    Admin configuration for the RehostedMedia model.
    This allows for easy viewing and management of the media file mappings
    through the Django admin interface, which is useful for debugging.
    """
    list_display = ('media_guid', 'file_path', 'content_type', 'created_at')
    search_fields = ('media_guid', 'file_path')
    list_filter = ('created_at', 'content_type')
    readonly_fields = ('media_guid', 'created_at')