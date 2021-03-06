from idaapi import *
from idautils import *
from aplib import decompress
import binascii
import struct

# hardcoding sucks :)
IMPORTS = { 'ntdll.dll' : ('ZwResumeThread', 'ZwQueryInformationProcess', 'ZwMapViewOfSection', 'ZwCreateSection', 'ZwClose', 'ZwUnmapViewOfSection', 'NtQueryInformationProcess', 'RtlAllocateHeap', 'RtlExitUserThread', 'RtlFreeHeap', 'RtlRandom','RtlReAllocateHeap', 'RtlSizeHeap', 'ZwQuerySection', 'RtlWalkHeap', 'NtDelayExecution'),
            'kernel32.dll' : ('GetModuleFileNameW', 'GetThreadContext', 'GetWindowsDirectoryW', 'GetModuleFileNameA', 'CopyFileA', 'CreateProcessA', 'ExpandEnvironmentStringsA', 'CreateProcessW', 'CreateThread', 'CreateToolhelp32Snapshot', 'DeleteFileW','DisconnectNamedPipe', 'ExitProcess', 'ExitThread', 'ExpandEnvironmentStringsW', 'FindCloseChangeNotification', 'FindFirstChangeNotificationW,FlushInstructionCache', 'FreeLibrary', 'GetCurrentProcessId', 'GetEnvironmentVariableA', 'GetEnvironmentVariableW', 'GetExitCodeProcess', 'GetFileSize', 'GetFileTime', 'GetModuleHandleA', 'GetModuleHandleW', 'GetProcAddress', 'GetProcessHeap', 'CreateNamedPipeA', 'GetSystemDirectoryW', 'GetTickCount', 'GetVersionExA', 'GetVolumeInformationA', 'GlobalLock', 'GlobalSize', 'GlobalUnlock', 'LoadLibraryA', 'LoadLibraryW', 'LocalFree', 'MultiByteToWideChar', 'OpenProcess', 'OpenThread', 'QueueUserAPC', 'ReadFile', 'ResumeThread', 'SetCurrentDirectoryW', 'SetEnvironmentVariableA', 'SetEnvironmentVariableW', 'SetErrorMode', 'SetFileAttributesW', 'SetFileTime', 'SuspendThread', 'TerminateProcess', 'Thread32First', 'Thread32Next', 'VirtualAlloc', 'VirtualFree', 'VirtualProtect', 'VirtualQuery', 'WaitForSingleObject', 'WriteFile', 'lstrcatA', 'lstrcatW', 'lstrcmpiW', 'lstrcpyA', 'lstrcpyW', 'lstrlenA', 'lstrlenW', 'CreateFileW', 'CreateFileA', 'ConnectNamedPipe', 'CloseHandle', 'GetShortPathNameW'),
            'advapi32.dll' : ('CheckTokenMembership', 'RegCloseKey', 'ConvertStringSidToSidA', 'ConvertStringSecurityDescriptorToSecurityDescriptorA', 'RegOpenKeyExA', 'RegSetValueExW', 'RegSetValueExA', 'RegSetKeySecurity', 'RegQueryValueExW', 'RegQueryValueExA', 'RegOpenKeyExW', 'RegNotifyChangeKeyValue', 'RegFlushKey', 'RegEnumValueW', 'RegEnumValueA', 'RegDeleteValueW', 'RegDeleteValueA', 'RegCreateKeyExW', 'RegCreateKeyExA'),
            'ws2_32.dll' : ('connect', 'shutdown', 'WSACreateEvent', 'closesocket', 'WSAStartup', 'WSAEventSelect', 'socket', 'sendto', 'recvfrom', 'getsockname', 'gethostbyname', 'listen', 'accept', 'WSASocketA', 'bind', 'htons'),
            'user32.dll' : ('wsprintfW', 'wsprintfA'),
            'ole32.dll' : ('CoInitialize'),
            'dnsapi.dll' : ('DnsWriteQuestionToBuffer_W', 'DnsRecordListFree', 'DnsExtractRecordsFromMessage_W')}

def calc_hash(string):
    return binascii.crc32(string) & 0xffffffff
    
def rc4crypt(data, key):
    x = 0
    box = bytearray(range(256))
    for i in range(256):
        x = (x + box[i] + key[i % len(key)]) % 256
        box[i], box[x] = box[x], box[i]
    x,y = 0, 0
    out = bytearray()
    for byte in data:
        x = (x + 1) % 256
        y = (y + box[x]) % 256
        box[x], box[y] = box[y], box[x]
        out += bytearray([byte ^ box[(box[x] + box[y]) % 256]])
    return out
    
def fix_payload_relocs_and_import(segment, relocs_offset):

    current_offset = 0
    
    # processing relocations
    while True:
     
        base = Dword(segment + relocs_offset + current_offset)
        size = Dword(segment + relocs_offset + current_offset + 4)
        
        if (base == 0 and current_offset != 0) or size == 0:
            current_offset += 4
            break
            
        current_offset += 8
        
        size = (size - 8) // 2
        
        for i in range(size):
            reloc = Word(segment + relocs_offset + current_offset)
            
            if reloc & 0x3000:
                reloc = reloc & 0xFFF
                PatchDword(segment + base + reloc, Dword(segment + base + reloc) + segment)
                SetFixup(segment + base + reloc, idaapi.FIXUP_OFF32 or idaapi.FIXUP_CREATED, 0, Dword(segment + base + reloc) + segment, 0)
                
            current_offset += 2
    
    # processing imports
    while True:
        
        module_hash = Dword(segment + relocs_offset + current_offset)
        import_offset = Dword(segment + relocs_offset + current_offset + 4)
        current_offset += 8
 
        if module_hash == 0 or import_offset == 0:
            break
        
        module = None
        for library in iter(IMPORTS):
            if module_hash == calc_hash(library.lower()):
                module = library
           
        while True:
            func_hash = Dword(segment + relocs_offset + current_offset)
            current_offset += 4
            if func_hash == 0:
                break
            
            if module is not None:
                for function in iter(IMPORTS[module]):
                    if func_hash == calc_hash(function):
                        MakeDword(segment + import_offset)
                        MakeName(segment + import_offset,  SegName(segment) + '_' + module.split('.')[0] + '_' + function)
            else:
                print('Import not found: module = 0x{0:08X}, function = 0x{1:08X}'.format(module_hash, func_hash))
                
            import_offset += 4
            
    return

def decrypt_payload(encrypted_addr, rc4key, encrypted_size, unpacked_size, entry_point, relocs, relocs_size):
    
    buffer = bytearray(encrypted_size)
    
    for i in range(len(buffer)):
        buffer[i] = Byte(encrypted_addr + i)
        
    decrypted = rc4crypt(buffer, rc4key)
    
    unpacked = decompress(str(decrypted)).do()
    
    # checking for free segment address
    seg_start = 0x10000000
    while SegName(seg_start) != '':
        seg_start += 0x10000000

    AddSeg(seg_start, seg_start + unpacked_size, 0, 1, idaapi.saRelPara, idaapi.scPub)
    
    # copying data to new segment
    data = unpacked[0]
    for i in range(len(data)):
        PatchByte(seg_start + i, ord(data[i]))
    
    fix_payload_relocs_and_import(seg_start, relocs)
    MakeFunction(seg_start + entry_point)
    
    return
    
def main():
    
    payload_addr = AskAddr(ScreenEA(), "Enter address of andromeda payload")
    
    if payload_addr != idaapi.BADADDR and payload_addr is not None:
        payload = bytearray(0x28)
        for i in range(len(payload)):
            payload[i] = Byte(payload_addr + i)
            
        dwords = struct.unpack_from('<LLLLLL', bytes(payload), 0x10)
        decrypt_payload(payload_addr + 0x28, payload[:16], dwords[0], dwords[2], dwords[3], dwords[4], dwords[5])
    
if __name__ == '__main__':
    main()
