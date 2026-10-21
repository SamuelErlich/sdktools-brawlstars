#import "SDKAssetExport.h"
#import <CommonCrypto/CommonDigest.h>

static NSData *SDKBoundedRead(NSURL *url, NSUInteger maximum) {
    if (!url || !url.isFileURL || maximum == 0) return nil;
    NSNumber *size = nil;
    if (![url getResourceValue:&size forKey:NSURLFileSizeKey error:nil] || size.unsignedLongLongValue > maximum) return nil;
    NSInputStream *stream = [NSInputStream inputStreamWithURL:url];
    if (!stream) return nil;
    NSMutableData *data = [NSMutableData dataWithCapacity:MIN(size.unsignedIntegerValue, maximum)];
    [stream open];
    uint8_t buffer[32768];
    BOOL failed = NO;
    while (YES) {
        NSInteger count = [stream read:buffer maxLength:MIN(sizeof(buffer), maximum - data.length + 1)];
        if (count < 0) { failed = YES; break; }
        if (count == 0) break;
        if ((NSUInteger)count > maximum - data.length) { failed = YES; break; }
        [data appendBytes:buffer length:(NSUInteger)count];
    }
    if (stream.streamError) failed = YES;
    [stream close];
    return failed ? nil : data;
}
static NSString *SDKAssetSHA256(NSData *data) {
    unsigned char digest[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256(data.bytes, (CC_LONG)data.length, digest);
    NSMutableString *text = [NSMutableString stringWithCapacity:64];
    for (NSUInteger index = 0; index < sizeof(digest); ++index) [text appendFormat:@"%02x", digest[index]];
    return text;
}
static BOOL SDKValidDigest(id text) {
    if (![text isKindOfClass:NSString.class] || [text length] != 64) return NO;
    NSCharacterSet *allowed = [NSCharacterSet characterSetWithCharactersInString:@"0123456789abcdef"];
    return [text rangeOfCharacterFromSet:allowed.invertedSet].location == NSNotFound;
}

@implementation SDKAssetExport
+ (NSDictionary *)exportTablesToDirectory:(NSURL *)directory {
    NSArray<NSString *> *allowed = @[@"res/csv_logic/characters.csv", @"res/csv_logic/skills.csv",
        @"res/csv_logic/projectiles_logic.csv", @"res/csv_logic/locations.csv", @"res/csv_logic/game_mode_variations.csv"];
    NSMutableArray *records = [NSMutableArray array];
    NSMutableArray<NSURL *> *files = [NSMutableArray array];
    if (NSThread.isMainThread) return @{@"records": @[], @"files": @[], @"status": @"wrong_thread"};
    NSURL *indexURL = [[NSBundle bundleForClass:self] URLForResource:@"AssetIndex" withExtension:@"json"];
    NSData *indexData = SDKBoundedRead(indexURL, 64 * 1024);
    id indexDocument = indexData ? [NSJSONSerialization JSONObjectWithData:indexData options:0 error:nil] : nil;
    NSMutableDictionary<NSString *, NSDictionary *> *entries = [NSMutableDictionary dictionary];
    BOOL validIndex = [indexDocument isKindOfClass:NSDictionary.class] && [indexDocument[@"schema_version"] isEqual:@1];
    id assets = validIndex ? indexDocument[@"assets"] : nil;
    validIndex = validIndex && [assets isKindOfClass:NSArray.class] && [assets count] == allowed.count;
    if (validIndex) for (id entry in assets) {
        if (![entry isKindOfClass:NSDictionary.class]) { validIndex = NO; break; }
        NSString *path = entry[@"relative_path"];
        id size = entry[@"bytes"];
        if (![path isKindOfClass:NSString.class] || ![allowed containsObject:path] || entries[path] ||
            ![size isKindOfClass:NSNumber.class] || [size longLongValue] <= 0 || [size unsignedLongLongValue] > 1024 * 1024 ||
            !SDKValidDigest(entry[@"sha256"])) { validIndex = NO; break; }
        entries[path] = entry;
    }
    validIndex = validIndex && entries.count == allowed.count;
    NSUInteger total = 0;
    for (NSString *path in allowed) {
        NSDictionary *entry = entries[path];
        NSMutableDictionary *record = [@{@"relative_path": path, @"scope": @"static_bundle_asset_not_live_match_state"} mutableCopy];
        if (!validIndex) { record[@"status"] = @"invalid_asset_index"; [records addObject:record]; continue; }
        NSUInteger expected = [entry[@"bytes"] unsignedIntegerValue];
        record[@"expected_bytes"] = @(expected);
        record[@"expected_sha256"] = entry[@"sha256"];
        for (NSString *key in @[@"column_count", @"logical_rows", @"named_records"])
            if ([entry[key] isKindOfClass:NSNumber.class] && [entry[key] longLongValue] >= 0)
                record[[@"indexed_" stringByAppendingString:key]] = entry[key];
        if (expected > 2 * 1024 * 1024 - total) {
            record[@"status"] = @"total_byte_limit"; [records addObject:record]; continue;
        }
        total += expected;
        NSURL *source = [NSBundle.mainBundle.resourceURL URLByAppendingPathComponent:path];
        NSData *bytes = SDKBoundedRead(source, expected);
        if (!bytes || bytes.length != expected) {
            record[@"status"] = @"missing_or_size_mismatch"; [records addObject:record]; continue;
        }
        NSString *digest = SDKAssetSHA256(bytes);
        if (![digest isEqualToString:entry[@"sha256"]]) {
            record[@"status"] = @"sha256_mismatch"; [records addObject:record]; continue;
        }
        NSURL *output = [directory URLByAppendingPathComponent:path.lastPathComponent];
        if (![bytes writeToURL:output options:NSDataWritingAtomic | NSDataWritingFileProtectionComplete error:nil]) {
            record[@"status"] = @"write_failed"; [records addObject:record]; continue;
        }
        record[@"status"] = @"exported"; record[@"bytes"] = @(bytes.length); record[@"sha256"] = digest;
        [records addObject:record]; [files addObject:output];
    }
    return @{@"records": [records copy], @"files": [files copy],
        @"status": files.count == allowed.count ? @"complete" : @"partial_or_unavailable"};
}
@end
