# sms_management_app/migrations/0003_make_uuid_primary.py

from django.db import migrations

class Migration(migrations.Migration):

    dependencies = [
        ('sms_management_app', '0002_ghltransmitsmsmapping_uuid'),
    ]

    operations = [
        # Remove old AutoField primary key
        migrations.RemoveField(
            model_name='ghltransmitsmsmapping',
            name='id',
        ),
        # Rename `uuid` field to `id` and make it primary key
        migrations.RenameField(
            model_name='ghltransmitsmsmapping',
            old_name='uuid',
            new_name='id',
        ),
    ]
