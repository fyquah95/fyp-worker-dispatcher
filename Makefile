.PHONY: worker
worker:
	jbuilder build worker/main.exe

.PHONY: controller
controller:
	jbuilder build controller/main.exe

.PHONY: test
test:
	jbuilder build test/main.exe
