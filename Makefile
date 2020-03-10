################################################################################
#
#   Makefile for TCKDB
#
################################################################################

test test-unittests:
	nosetests --nocapture --nologcapture --all-modules --verbose --with-coverage --cover-inclusive --cover-package=tckdb --cover-erase --cover-html --cover-html-dir=testing/coverage
