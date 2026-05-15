#import <XCTest/XCTest.h>
#import "Dog.h"

@interface DogTests : XCTestCase
@end

@implementation DogTests
- (void)testSpeak {
    Dog *d = [[Dog alloc] initWithName:@"Rex"];
    XCTAssertNotNil([d speak]);
}
@end
