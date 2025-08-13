from django.db import models
import uuid

# Create your models here.

class GHLAuthCredentials(models.Model):
    user_id = models.CharField(max_length=255)
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

    business_email = models.EmailField(null=True, blank=True, help_text="Business email for TransmitSMS account")
    business_phone = models.CharField(max_length=20, null=True, blank=True, help_text="Business phone number in E.164 format")
    contact_name = models.CharField(max_length=255, null=True, blank=True, help_text="Primary contact name")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user_id} - {self.company_id}"