#!/usr/bin/env python3
"""
AWS Lambda handler for HMS forecast workflow.

This module provides a Lambda-compatible interface to the forecast workflow,
handling event parsing, S3 integration, and response formatting.
"""

import json
import logging
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Dict

# Ensure the Lambda task root is in the path
sys.path.insert(0, os.environ.get("LAMBDA_TASK_ROOT", "/var/task"))

# Import forecast modules
from run import main as forecast_main, parse_args
from forecast_logging import setup_json_logging

# Set up logging for Lambda
logger = setup_json_logging(level=logging.INFO)


def _to_bool(value: Any, default: bool = False) -> bool:
    """Convert common JSON/string boolean representations safely."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    return bool(value)


def parse_lambda_event(event: Dict[str, Any]) -> list[str]:
    """
    Parse Lambda event and convert to command-line arguments for forecast workflow.

    Event structure:
    {
        "model_path": "/mnt/model/Trinity_Forecast.hms",
        "download": true,
        "mode": "test",  # Optional: test, validation1, validation2, validation3
        "skip_model": false,  # Optional: skip model vault download
        "upload": true,  # Optional: upload results to S3
        "lookback_control": "Lookback",  # Optional: default "Lookback"
        "forecast_control": "Forecast",  # Optional: default "Forecast"
        "single_run": false,  # Optional: use single-run mode
        "debug": false  # Optional: enable debug logging
    }

    Returns:
        List of command-line arguments for the forecast workflow
    """
    args = []

    # Download options
    if event.get("download", True):
        args.append("--download")
    else:
        args.append("--no-download")

    # Mode selection
    if "mode" in event:
        args.extend(["--mode", event["mode"]])

    # Skip model option
    if event.get("skip_model", False):
        args.append("--skip-model")

    # Upload options
    if "upload" in event:
        if event["upload"]:
            args.append("--upload")
        else:
            args.append("--no-upload")

    # Workflow mode
    if event.get("single_run", False):
        args.append("--single-run")

    # Control file names
    if "lookback_control" in event:
        args.extend(["--lookback-control", event["lookback_control"]])
    if "forecast_control" in event:
        args.extend(["--forecast-control", event["forecast_control"]])

    # Debug mode
    if event.get("debug", False):
        args.append("--debug")

    # Model path (required)
    model_path = event.get("model_path", "/tmp/model/Trinity_Forecast.hms")
    args.append(model_path)

    return args


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    AWS Lambda handler for HMS forecast workflow.

    Args:
        event: Lambda event containing forecast parameters
        context: Lambda context object

    Returns:
        Response dictionary with status and results
    """
    request_id = (
        getattr(context, "aws_request_id", getattr(context, "request_id", "local"))
        if context
        else "local"
    )
    build_version = os.environ.get("BUILD_VERSION", "unknown")
    hms_version = os.environ.get("HMS_VERSION", "unknown")
    # Default to API-style error responses (no raised exception/stack trace).
    # Set fail_on_error=true in the event to force Lambda invocation failure.
    fail_on_error = _to_bool(event.get("fail_on_error"), default=False)

    logger.info(
        f"Lambda forecast handler invoked [request_id={request_id}]",
        extra={
            "event": event,
            "build_version": build_version,
            "hms_version": hms_version,
        },
    )

    try:
        # Parse the Lambda event into command-line arguments
        args = parse_lambda_event(event)
        logger.info(f"Parsed arguments [request_id={request_id}]: {args}")

        # Override sys.argv to simulate command-line invocation
        original_argv = sys.argv
        sys.argv = ["forecast"] + args

        # Run the forecast workflow
        logger.info(f"Starting forecast workflow [request_id={request_id}]")
        exit_code = forecast_main()
        # Restore original sys.argv
        sys.argv = original_argv

        if exit_code == 0:
            logger.info(
                f"Forecast workflow completed successfully [request_id={request_id}]"
            )
            return {
                "statusCode": 200,
                "body": json.dumps(
                    {
                        "status": "success",
                        "message": "Forecast workflow completed successfully",
                        "request_id": request_id,
                        "exit_code": exit_code,
                    }
                ),
            }
        else:
            message = f"Forecast workflow failed with exit code {exit_code}"
            logger.error(
                f"Forecast workflow failed [request_id={request_id}] [exit_code={exit_code}]"
            )
            if fail_on_error:
                raise RuntimeError(message)
            return {
                "statusCode": 500,
                "body": json.dumps(
                    {
                        "status": "error",
                        "message": message,
                        "request_id": request_id,
                        "exit_code": exit_code,
                    }
                ),
            }

    except Exception as e:
        # Ensure argv is restored if an exception occurred before normal restoration.
        if "original_argv" in locals():
            sys.argv = original_argv
        logger.error(
            f"Lambda handler exception [request_id={request_id}]: {str(e)}",
            extra={"traceback": traceback.format_exc()},
        )
        if fail_on_error:
            raise
        return {
            "statusCode": 500,
            "body": json.dumps(
                {
                    "status": "error",
                    "message": f"Lambda handler exception: {str(e)}",
                    "request_id": request_id,
                    "traceback": traceback.format_exc(),
                }
            ),
        }


# For local testing
if __name__ == "__main__":
    # Mock Lambda context
    class MockContext:
        aws_request_id = "local-test"
        request_id = "local-test"
        function_name = "hms-forecast"
        memory_limit_in_mb = 10240
        invoked_function_arn = (
            "arn:aws:lambda:us-east-1:123456789012:function:hms-forecast"
        )

    # Example event
    test_event = {
        "model_path": "/tmp/model/Trinity_Forecast.hms",
        "download": True,
        "mode": "test",
        "upload": True,
        "debug": False,
    }

    print("Testing Lambda handler locally...")
    print(f"Event: {json.dumps(test_event, indent=2)}")

    result = handler(test_event, MockContext())
    print(f"\nResult: {json.dumps(result, indent=2)}")
