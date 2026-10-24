"""Keep the test process off Atlas and off the control-plane flags.

This runs before test modules import the app. It does not print any URI.
"""
import os

os.environ['HISTORYIT_PYTEST'] = '1'
os.environ['DATABASE_CONTROL_PLANE_ENABLED'] = 'false'
os.environ['DATABASE_SHADOW_WRITES_ENABLED'] = 'false'
os.environ['DATABASE_BACKFILL_ENABLED'] = 'false'
os.environ['DATABASE_USAGE_INGESTION_ENABLED'] = 'false'
os.environ['MONGODB_URI'] = ''
os.environ['CONTROL_PLANE_ENVIRONMENT'] = 'staging'
