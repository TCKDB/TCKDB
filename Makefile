################################################################################
#
#   Makefile for TCKDB
#
################################################################################

.PHONY: up wait-db-docker wait-app-docker test-all-docker test-schema-docker test-models-docker test-endpoints-docker down
.PHONY: test-all test-schema test-models test-endpoints

# Docker-based targets
up:
	docker-compose up -d --build

wait-db-docker:
	docker-compose exec test-db bash -c "until pg_isready -U $(grep POSTGRES_USER ./tckdb/backend/app/core/.env.test | cut -d '=' -f2); do sleep 1; done"

wait-app-docker:
	docker-compose exec app bash -c "while ! nc -z localhost 8000; do sleep 1; done"

test-all-docker:
	export TESTING=true && \
	docker-compose exec app bash -c "conda run -n tck_env pytest -ra -vv /code/tckdb/backend/app/tests/schemas" && \
	docker-compose exec app bash -c "conda run -n tck_env pytest -ra -vv /code/tckdb/backend/app/tests/models" && \
	docker-compose exec app bash -c "conda run -n tck_env pytest -ra -vv /code/tckdb/backend/app/tests/endpoints"

test-schema-docker:
	export TESTING=true && \
	docker-compose exec app bash -c "conda run -n tck_env pytest -ra -vv /code/tckdb/backend/app/tests/schemas"

test-models-docker:
	export TESTING=true && \
	docker-compose exec app bash -c "conda run -n tck_env pytest -ra -vv /code/tckdb/backend/app/tests/models"

test-endpoints-docker:
	export TESTING=true && \
	docker-compose exec app bash -c "conda run -n tck_env pytest -ra -vv /code/tckdb/backend/app/tests/endpoints"

down:
	docker-compose down -v

# Non-Docker (Conda-based) targets
test-all:
	@echo "Running all tests in Conda environment"
	@TESTING=true conda run -n tck_env pytest -ra -vv ./tckdb/backend/app/tests/schemas
	@TESTING=true conda run -n tck_env pytest -ra -vv ./tckdb/backend/app/tests/models
	@TESTING=true conda run -n tck_env pytest -ra -vv ./tckdb/backend/app/tests/endpoints

test-schemas:
	@echo "Running schema tests in Conda environment"
	@TESTING=true conda run -n tck_env pytest -ra -vv ./tckdb/backend/app/tests/schemas

test-models:
	@echo "Running models tests in Conda environment"
	@TESTING=true conda run -n tck_env pytest -ra -vv ./tckdb/backend/app/tests/models

test-endpoints:
	@echo "Running endpoints tests in Conda environment"
	@TESTING=true conda run -n tck_env pytest -ra -vv ./tckdb/backend/app/tests/endpoints

test-lint:
	@echo "Running lint tests in Conda environment"
	@conda run -n tck_env trunk check
