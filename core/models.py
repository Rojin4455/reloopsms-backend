from django.db import models
import uuid

# Create your models here.

class GHLAuthCredentials(models.Model):
    user_id = models.CharField(max_length=255, unique=True)
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    access_token = models.TextField()
    refresh_token = models.TextField()
    expires_in = models.IntegerField()
    scope = models.CharField(max_length=500, null=True, blank=True)
    user_type = models.CharField(max_length=50, null=True, blank=True)
    company_id = models.CharField(max_length=255, null=True, blank=True)
    location_name = models.CharField(max_length=255, null=True, blank=True)
    timezone = models.CharField(max_length=100, null=True, blank=True, default="")
    location_id = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user_id} - {self.company_id}"