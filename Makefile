# hagema-agent — perintah umum
.PHONY: install setup run model doctor test

install:
	./install.sh

setup:
	./.venv/bin/hagema setup

run:
	./.venv/bin/hagema

model:
	./.venv/bin/hagema model

doctor:
	./.venv/bin/hagema doctor

test:
	./.venv/bin/python -m unittest discover tests -v
