#!/bin/bash
cd /home/kavia/workspace/code-generation/voltguard-energy-dashboard-242137/voltguard_frontend
npm run build
EXIT_CODE=$?
if [ $EXIT_CODE -ne 0 ]; then
   exit 1
fi

