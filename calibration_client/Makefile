# UC2 Calib Makefile

TARGET		:=	uc2_calib
BUILD		:=	build
SOURCES		:=	source
RESOURCES	:=	../resources

APP_TITLE	:=	UC2 Calib
APP_DESCRIPTION	:=	UC2 Calibration
APP_AUTHOR	:=	cylin577
APP_PRODCODE:=	CTR-P-UC2C
APP_HTID	:=	0x038B2

VERSION_MAJOR	:= 0
VERSION_MINOR	:= 1
VERSION_PATCH	:= 0

#---------------------------------------------------------------------------------
# Options for code generation
#---------------------------------------------------------------------------------
ARCH		:=	-march=armv6k -mtune=mpcore -mfloat-abi=hard -mtp=soft

CFLAGS	:=	-g -Wall -O2 -mword-relocations \
			-ffunction-sections \
			$(ARCH)

CFLAGS	+=	$(INCLUDE) -D__3DS__

CXXFLAGS	:= $(CFLAGS) -fno-rtti -fno-exceptions -std=gnu++11

ASFLAGS		:=	-g $(ARCH)
LDFLAGS	= 	-specs=3dsx.specs -g $(ARCH) -Wl,-Map,$(notdir $*.map) 

LIBS		:=	-lctru -lm

#---------------------------------------------------------------------------------
# path to tools - assuming /opt/devkitpro if not set
#---------------------------------------------------------------------------------
export DEVKITPRO ?= /opt/devkitpro
export DEVKITARM ?= $(DEVKITPRO)/devkitARM

include $(DEVKITARM)/3ds_rules

#---------------------------------------------------------------------------------
# list of directories containing libraries, this must be the top level containing
# include and lib
#---------------------------------------------------------------------------------
LIBDIRS		:=	$(CTRULIB)

#---------------------------------------------------------------------------------
# no real need to edit anything past this point unless you need to add additional
# rules for different file extensions
#---------------------------------------------------------------------------------
ifneq ($(BUILD),$(notdir $(CURDIR)))

export OUTPUT		:=	$(CURDIR)/$(TARGET)
export TOPDIR		:=	$(CURDIR)

export VPATH		:=	$(foreach dir,$(SOURCES),$(CURDIR)/$(dir))
export DEPSDIR	:=	$(CURDIR)/$(BUILD)

CFILES		:=	$(foreach dir,$(SOURCES),$(notdir $(wildcard $(dir)/*.c)))
CPPFILES	:=	$(foreach dir,$(SOURCES),$(notdir $(wildcard $(dir)/*.cpp)))
SFILES		:=	$(foreach dir,$(SOURCES),$(notdir $(wildcard $(dir)/*.s)))
BINFILES	:=	$(foreach dir,$(SOURCES),$(notdir $(wildcard $(dir)/*.bin)))

#---------------------------------------------------------------------------------
# use CXX for linking C++ projects, CC for pure C
#---------------------------------------------------------------------------------
ifeq ($(strip $(CPPFILES)),)
	export LD		:=	$(CC)
else
	export LD		:=	$(CXX)
endif

export OFILES	:=	$(addsuffix .o,$(BINFILES)) \
				$(CPPFILES:.cpp=.o) $(CFILES:.c=.o) $(SFILES:.s=.o)

export INCLUDE	:=	$(foreach dir,$(INCLUDES),-I$(CURDIR)/$(dir)) \
				$(foreach dir,$(LIBDIRS),-I$(dir)/include) \
				-I$(CURDIR)/$(BUILD)

export LIBPATHS	:=	$(foreach dir,$(LIBDIRS),-L$(dir)/lib)

.PHONY: $(BUILD) clean all generate 3dsx

#---------------------------------------------------------------------------------
all: $(BUILD)

$(BUILD):
	@[ -d $@ ] || mkdir -p $@
	@$(MAKE) --no-print-directory -C $(BUILD) -f $(CURDIR)/Makefile 3dsx

#---------------------------------------------------------------------------------
3dsx: generate $(BUILD)
	@$(MAKE) --no-print-directory -C $(BUILD) -f $(CURDIR)/Makefile $(TARGET).3dsx

#---------------------------------------------------------------------------------
clean:
	@echo clean ...
	@rm -fr $(BUILD) $(TARGET).3dsx $(TARGET).elf $(TARGET).cia $(TARGET).smdh $(RESOURCES)/banner.bnr $(RESOURCES)/icon.bin

#---------------------------------------------------------------------------------
generate:
	@echo Generating banner.bnr and icon.bin...
	@bannertool makesmdh -s "$(APP_TITLE)" -l "$(APP_DESCRIPTION)" -p "$(APP_AUTHOR)" -i $(RESOURCES)/icon.png -o $(RESOURCES)/icon.bin 2>/dev/null || \
		smdhtool --create "$(APP_TITLE)" "$(APP_DESCRIPTION)" "$(APP_AUTHOR)" $(TARGET).smdh
	@bannertool makebanner -i $(RESOURCES)/banner.png -a $(RESOURCES)/audio.wav -o $(RESOURCES)/banner.bnr 2>/dev/null || \
		bannertool makebanner -t "$(APP_TITLE)" -d "$(APP_DESCRIPTION)" -o $(RESOURCES)/banner.bnr 2>/dev/null || true

#---------------------------------------------------------------------------------
$(BUILD)/$(TARGET).rsf: $(RESOURCES)/$(TARGET).rsf $(BUILD)
	@echo "Generating $(notdir $@)..."
	@sed -e 's|@@APP_TITLE@@|$(APP_TITLE)|g' \
		-e 's|@@APP_PRODCODE@@|$(APP_PRODCODE)|g' \
		-e 's|@@APP_HTID@@|$(APP_HTID)|g' \
		$< > $@

#---------------------------------------------------------------------------------
cia: generate $(BUILD)/$(TARGET).rsf $(BUILD)
	@$(MAKE) --no-print-directory -C $(BUILD) -f $(CURDIR)/Makefile $(TARGET).elf
	@makerom -f cia -o $(TARGET).cia -elf $(BUILD)/$(TARGET).elf -rsf $(BUILD)/$(TARGET).rsf \
		-icon $(RESOURCES)/icon.bin -banner $(RESOURCES)/banner.bnr -exefslogo -target t \
		-major $(VERSION_MAJOR) -minor $(VERSION_MINOR) -micro $(VERSION_PATCH)
	@echo "built ... $(TARGET).cia"

else

3dsx: $(OUTPUT).3dsx

$(OUTPUT).3dsx		: 	$(OUTPUT).elf

$(OUTPUT).elf		: 	$(OFILES)

%.bin.o		: 	%.bin
	@echo $(notdir $<)
	@$(bin2o)

#---------------------------------------------------------------------------------
# rules for assembling GPU shaders
#---------------------------------------------------------------------------------
define shader-as
	$(eval CURBIN := $(patsubst %.shbin.o,%.shbin,$(notdir $@)))
	picasso -o $(CURBIN) $1
	bin2s $(CURBIN) | $(AS) -o $@
	echo "extern const u8" `(echo $(CURBIN) | sed -e 's/^]([0-9])/_]1/' | tr . _)`"_end[];" > `(echo $(CURBIN) | tr . _)`".h"
	echo "extern const u8" `(echo $(CURBIN) | sed -e 's/^]([0-9])/_]1/' | tr . _)`"[];" >> `(echo $(CURBIN) | tr . _)`".h"
	echo "extern const u32" `(echo $(CURBIN) | sed -e 's/^]([0-9])/_]1/' | tr . _)`"_size";" >> `(echo $(CURBIN) | tr . _)`".h"
endef

%.shbin.o : %.v.pica
	@echo $(notdir $<)
	@$(call shader-as,$<)

%.shbin.o : %.g.pica
	@echo $(notdir $<)
	@$(call shader-as,$<)

#---------------------------------------------------------------------------------

-include $(DEPSDIR)/*.d

endif