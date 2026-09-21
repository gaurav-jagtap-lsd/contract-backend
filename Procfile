web: gunicorn contractvault_api.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --timeout 120
worker: celery -A celery_app worker --loglevel=info --concurrency=2
beat: celery -A celery_app beat --loglevel=info --scheduler django_celery_beat.schedulers:DatabaseScheduler
