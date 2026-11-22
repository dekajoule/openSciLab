; Hello, world! on the LCD, then the four LEDs on PA0-PA3 count up.
; W65C02 breadboard computer V1.0: RAM $0000-$3FFF, VIA $6000, ROM $8000-$FFFF.
; Assemble (vasm, http://sun.hasenbraten.de/vasm/):
;   vasm6502_oldstyle -Fbin -dotdir -wdc02 hello.s -o hello.bin
; hello.bin is the 32 KB image for the EEPROM (programmer: offset 0 = CPU address $8000);
; hello.hex is the same as Intel HEX. Both work as the Image of U3.

PORTB = $6000           ; VIA port B: LCD DB0-DB7
PORTA = $6001           ; VIA port A: PA7 E, PA6 RW, PA5 RS, PA0-PA3 the LEDs
DDRB  = $6002
DDRA  = $6003

E  = %10000000
RW = %01000000
RS = %00100000

count = $00             ; in RAM (zero page): the LED counter

  .org $8000

reset:
  ldx #$ff
  txs                   ; the stack: $01FF down (RAM)
  lda #%11111111
  sta DDRB              ; port B: outputs
  lda #%11101111
  sta DDRA              ; port A: E, RW, RS and the LEDs outputs (PA4 free)

  lda #%00111000        ; 8-bit interface, 2 lines, 5x8 dots
  jsr lcd_instruction
  lda #%00001100        ; display on, no cursor
  jsr lcd_instruction
  lda #%00000110        ; move right, no shift
  jsr lcd_instruction
  lda #%00000001        ; clear
  jsr lcd_instruction

  ldx #0
print:
  lda message,x
  beq counting
  jsr print_char
  inx
  bra print

counting:
  stz count
loop:
  lda count
  and #%00001111        ; the LEDs show the low four bits, E, RW, RS stay low
  sta PORTA
  ldy #20
delay:
  dey
  bne delay
  inc count
  bra loop

message: .asciiz "Hello, world!"

lcd_wait:               ; until the busy flag is clear
  pha
  lda #%00000000
  sta DDRB              ; port B: inputs
lcd_busy:
  lda #RW
  sta PORTA
  lda #(RW | E)
  sta PORTA
  lda PORTB
  and #%10000000
  bne lcd_busy
  lda #RW
  sta PORTA
  lda #%11111111
  sta DDRB
  pla
  rts

lcd_instruction:
  jsr lcd_wait
  sta PORTB
  lda #0
  sta PORTA
  lda #E
  sta PORTA
  lda #0
  sta PORTA
  rts

print_char:
  jsr lcd_wait
  sta PORTB
  lda #RS
  sta PORTA
  lda #(RS | E)
  sta PORTA
  lda #RS
  sta PORTA
  rts

  .org $fffc
  .word reset
  .word $0000
